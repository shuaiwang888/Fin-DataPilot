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
from app.memory import build_memory_context, update_memory_after_turn
from app.security import AuthContext, require_user
from app.skills.configuration import refresh_published_skill_configuration
from app.storage.repository import (
    append_agent_run_event_async,
    create_agent_run_async,
    create_session_async,
    finish_agent_run_async,
    get_agent_run_for_user_async,
    get_session_for_user_async,
    list_messages_async,
    request_agent_run_cancel_async,
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


async def _execute_run_background(
    *,
    run_id: str,
    session_id: str,
    user_id: str,
    query: str,
    history: list[dict[str, Any]],
    memory_context: str,
    event_queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    """Own an Agent run independently from the transient SSE connection.

    Hugging Face's proxy can close a long request even while heartbeat events
    are flowing. Keeping execution here lets the browser recover the durable
    result through ``GET /runs/{run_id}`` without repeating paid tool calls.
    """
    final_text = ""
    tool_calls: list[dict[str, Any]] = []
    terminal_status = "completed"
    terminal_error: str | None = None

    async def publish(event: dict[str, Any]) -> None:
        try:
            await append_agent_run_event_async(run_id, event)
        except Exception:  # noqa: BLE001
            # A trace-write failure must not throw away an otherwise viable
            # answer. The terminal run row is persisted separately below.
            logger.exception("event persistence failed for run %s", run_id)
        await event_queue.put(event)

    async def collect() -> None:
        nonlocal final_text
        async for item in run_agent_stream(
            user_query=query,
            history=history,
            session_id=session_id,
            run_id=run_id,
            memory_context=memory_context,
        ):
            event_name = str(item.get("event", ""))
            event_data = item.get("data", {})
            event = {"event": event_name, "data": event_data}
            await publish(event)
            if event_name == "tool_result":
                tool_calls.append(event_data)
            elif event_name == "message_final":
                final_text = str(event_data.get("content", ""))

    try:
        await asyncio.wait_for(collect(), timeout=get_settings().agent_run_timeout_seconds)
    except asyncio.TimeoutError:
        terminal_status = "failed"
        terminal_error = "Agent run exceeded the server time limit"
        await publish({"event": "error", "data": {"message": terminal_error}})
    except asyncio.CancelledError:
        terminal_status = "cancelled"
        terminal_error = "run cancelled"
    except Exception as exc:  # noqa: BLE001
        logger.exception("run %s failed", run_id)
        terminal_status = "failed"
        terminal_error = str(exc)
        await publish({"event": "error", "data": {"message": terminal_error}})

    try:
        if final_text and terminal_status == "completed":
            try:
                await save_message_async(
                    session_id=session_id,
                    role="assistant",
                    content=final_text,
                    tool_calls=tool_calls or None,
                    thinking={"trace": tool_calls},
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("assistant message persistence failed for run %s", run_id)
                terminal_status = "failed"
                terminal_error = f"answer persistence failed: {exc}"

        try:
            await finish_agent_run_async(run_id, terminal_status, final_text or None, terminal_error)
        except Exception as exc:  # noqa: BLE001
            logger.exception("terminal status persistence failed for run %s", run_id)
            terminal_status = "failed"
            terminal_error = f"run persistence failed: {exc}"

        await event_queue.put(
            {
                "event": "run_status",
                "data": {"run_id": run_id, "status": terminal_status, "error": terminal_error},
            }
        )
        await event_queue.put(None)

        # Durable status is available before best-effort memory extraction,
        # so frontend recovery does not wait for this post-processing step.
        if final_text and terminal_status == "completed":
            try:
                await asyncio.wait_for(
                    update_memory_after_turn(
                        user_id=user_id,
                        session_id=session_id,
                        query=query,
                        answer=final_text,
                        message_count=len(history) + 2,
                    ),
                    timeout=20,
                )
            except Exception:  # noqa: BLE001
                logger.exception("memory update failed for run %s", run_id)
    finally:
        # Always release the strong task reference, including if terminal
        # persistence itself encounters an unexpected failure.
        await ACTIVE_RUNS.unregister(run_id)


@router.post("/chat/stream", response_class=StreamingResponse)
@limiter.limit("12/minute")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    auth: AuthContext = Depends(require_user),
) -> Any:
    """Start one run. Identity is derived only from the bearer token."""
    await refresh_published_skill_configuration()

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
        # The message just saved is always last. Slice only that row; filtering
        # by content would also erase legitimate repeated questions.
        history = history[:-1]
        memory_context = await build_memory_context(auth.user_id, session_id, body.query)
        run_id = await create_agent_run_async(session_id, auth.user_id, body.query)
        yield _sse("run", {"run_id": run_id, "session_id": session_id, "status": "running"}, run_id)
        yield _sse("ping", {"ts": time.time()})

        agent_q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        runner_task: asyncio.Task[Any] = asyncio.create_task(
            _execute_run_background(
                run_id=run_id,
                session_id=session_id,
                user_id=auth.user_id,
                query=body.query,
                history=history,
                memory_context=memory_context,
                event_queue=agent_q,
            )
        )
        await ACTIVE_RUNS.register(run_id, runner_task)

        # This loop is only a subscriber. Losing the HTTP connection must not
        # cancel ``runner_task``; the frontend will recover its durable result.
        waiters: set[asyncio.Task[Any]] = set()
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("SSE detached from run %s; background execution continues", run_id)
                    return
                queue_wait = asyncio.create_task(agent_q.get())
                keepalive_wait = asyncio.create_task(asyncio.sleep(SSE_KEEPALIVE_INTERVAL))
                disconnect_wait = asyncio.create_task(asyncio.sleep(1))
                waiters = {queue_wait, keepalive_wait, disconnect_wait}
                done, pending = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    with suppress(asyncio.CancelledError):
                        await task
                waiters = set()
                if queue_wait in done:
                    item = queue_wait.result()
                    if item is None:
                        break
                    yield _sse(str(item.get("event", "")), item.get("data", {}), item.get("id"))
                elif keepalive_wait in done:
                    yield _sse_keepalive()
                    yield _sse("heartbeat", {"ts": time.time(), "run_id": run_id})
        finally:
            for task in waiters:
                task.cancel()
            for task in waiters:
                with suppress(asyncio.CancelledError):
                    await task

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
    if run["status"] != "running":
        return {"run_id": body.run_id, "cancelled": False}
    requested = await request_agent_run_cancel_async(body.run_id, auth.user_id)
    cancelled = await ACTIVE_RUNS.cancel(body.run_id)
    if cancelled:
        await finish_agent_run_async(body.run_id, "cancelled", error="cancelled by user")
    return {"run_id": body.run_id, "cancelled": cancelled, "cancellation_requested": requested}


@router.get("/runs/{run_id}")
@limiter.limit("60/minute")
async def get_run(
    run_id: str, request: Request, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    run = await get_agent_run_for_user_async(run_id, auth.user_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run
