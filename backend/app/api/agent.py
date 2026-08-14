"""Authenticated, cancellable agent chat streaming endpoints (SSE)."""
import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.agent.graph import run_agent_stream
from app.agent.runtime import ACTIVE_RUNS
from app.config import get_settings
from app.security import AuthContext, require_user
from app.storage.repository import (
    append_agent_run_event_async,
    create_agent_run_async,
    create_session_async,
    finish_agent_run_async,
    get_agent_run_for_user_async,
    get_session_for_user_async,
    list_messages_async,
    save_message_async,
)

router = APIRouter()
logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)
SSE_KEEPALIVE_INTERVAL = 15.0


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8_000)
    session_id: str | None = None


class StopRequest(BaseModel):
    run_id: str


def _sse(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_keepalive() -> str:
    return ": keep-alive\n\n"


async def _persist_and_encode(run_id: str, event: dict[str, Any]) -> str:
    await append_agent_run_event_async(run_id, event)
    return _sse(event["event"], event.get("data", {}), event.get("id"))


@router.post("/chat/stream", response_class=StreamingResponse)
@limiter.limit("12/minute")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    auth: AuthContext = Depends(require_user),
) -> Any:
    """Start one run. Identity is derived only from the bearer token."""

    async def event_gen() -> AsyncIterator[str]:
        session_id = body.session_id
        if session_id:
            if not await get_session_for_user_async(session_id, auth.user_id):
                yield _sse("error", {"message": "Session not found"})
                return
        else:
            session_id = await create_session_async(
                title=body.query[:30] or "新对话", user_id=auth.user_id
            )
            yield _sse("session", {"session_id": session_id, "title": body.query[:30] or "新对话"})

        await save_message_async(session_id=session_id, role="user", content=body.query)
        history = await list_messages_async(session_id)
        history = [m for m in history if not (m["role"] == "user" and m["content"] == body.query)]
        run_id = await create_agent_run_async(session_id, auth.user_id, body.query)
        yield _sse("run", {"run_id": run_id, "session_id": session_id, "status": "running"}, run_id)
        yield _sse("ping", {"ts": time.time()})

        stop = asyncio.Event()
        ticker_q: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        agent_q: asyncio.Queue[dict[str, Any] | BaseException | None] = asyncio.Queue(maxsize=64)

        async def ticker() -> None:
            try:
                while not stop.is_set():
                    await asyncio.sleep(SSE_KEEPALIVE_INTERVAL)
                    if not stop.is_set() and ticker_q.empty():
                        ticker_q.put_nowait(None)
            except asyncio.CancelledError:
                return

        async def pump() -> None:
            async def collect() -> None:
                async for event in run_agent_stream(
                    user_query=body.query, history=history, session_id=session_id, run_id=run_id
                ):
                    await agent_q.put(event)
            try:
                await asyncio.wait_for(collect(), timeout=get_settings().agent_run_timeout_seconds)
            except asyncio.TimeoutError:
                await agent_q.put(RuntimeError("Agent run exceeded the server time limit"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await agent_q.put(exc)
            finally:
                await agent_q.put(None)

        ticker_task: asyncio.Task[Any] = asyncio.create_task(ticker())
        pump_task: asyncio.Task[Any] = asyncio.create_task(pump())
        await ACTIVE_RUNS.register(run_id, pump_task)
        final_text = ""
        tool_calls: list[dict[str, Any]] = []
        terminal_status = "completed"
        terminal_error: str | None = None
        try:
            while True:
                if await request.is_disconnected():
                    terminal_status = "cancelled"
                    terminal_error = "client disconnected"
                    break
                queue_wait = asyncio.create_task(agent_q.get())
                tick_wait = asyncio.create_task(ticker_q.get())
                disconnect_wait = asyncio.create_task(asyncio.sleep(1))
                done, pending = await asyncio.wait(
                    {queue_wait, tick_wait, disconnect_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    with suppress(asyncio.CancelledError):
                        await task
                if queue_wait in done:
                    item = queue_wait.result()
                    if item is None:
                        break
                    if isinstance(item, BaseException):
                        raise item
                    event_name = str(item.get("event", ""))
                    event_data = item.get("data", {})
                    event = {"event": event_name, "data": event_data}
                    yield await _persist_and_encode(run_id, event)
                    if event_name == "tool_result":
                        tool_calls.append(event_data)
                    elif event_name == "message_final":
                        final_text = str(event_data.get("content", ""))
                elif tick_wait in done:
                    yield _sse_keepalive()
                    yield _sse("heartbeat", {"ts": time.time(), "run_id": run_id})
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            terminal_error = "run cancelled"
        except Exception as exc:  # noqa: BLE001
            logger.exception("run %s failed", run_id)
            terminal_status = "failed"
            terminal_error = str(exc)
            yield await _persist_and_encode(run_id, {"event": "error", "data": {"message": str(exc)}})
        finally:
            stop.set()
            for task in (ticker_task, pump_task):
                task.cancel()
            for task in (ticker_task, pump_task):
                with suppress(asyncio.CancelledError, Exception):
                    await task
            await ACTIVE_RUNS.unregister(run_id)
            await finish_agent_run_async(run_id, terminal_status, final_text or None, terminal_error)

        if final_text and terminal_status == "completed":
            await save_message_async(
                session_id=session_id,
                role="assistant",
                content=final_text,
                tool_calls=tool_calls or None,
                thinking={"trace": tool_calls},
            )
        yield _sse("run_status", {"run_id": run_id, "status": terminal_status, "error": terminal_error})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/chat/stop")
@limiter.limit("30/minute")
async def stop_chat_run(
    body: StopRequest, request: Request, auth: AuthContext = Depends(require_user)
) -> dict[str, object]:
    run = await get_agent_run_for_user_async(body.run_id, auth.user_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    cancelled = await ACTIVE_RUNS.cancel(body.run_id)
    if cancelled:
        await finish_agent_run_async(body.run_id, "cancelled", error="cancelled by user")
    return {"run_id": body.run_id, "cancelled": cancelled}


@router.get("/runs/{run_id}")
@limiter.limit("60/minute")
async def get_run(
    run_id: str, request: Request, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    run = await get_agent_run_for_user_async(run_id, auth.user_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run
