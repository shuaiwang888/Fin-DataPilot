"""Agent chat streaming endpoint (SSE)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.graph import run_agent_stream
from app.storage.repository import (
    create_session_async,
    get_session_async,
    list_messages_async,
    save_message_async,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# SSE-level keep-alive: emit a comment every K seconds so intermediate
# proxies (HF Space, Cloudflare, nginx) don't kill an idle connection
# while the agent is thinking. The client ignores SSE comments.
SSE_KEEPALIVE_INTERVAL = 15.0


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None
    user_id: str = "default"


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_keepalive() -> str:
    """SSE comment line — clients ignore it but the connection stays alive."""
    return ": keep-alive\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """Stream agent progress as Server-Sent Events."""

    async def event_gen():
        session_id = body.session_id
        if not session_id or not await get_session_async(session_id):
            session_id = await create_session_async(
                title=(body.query[:30] if body.query else "新对话") or "新对话",
                user_id=body.user_id,
            )
            yield _sse("session", {"session_id": session_id, "title": body.query[:30] or "新对话"})

        # Persist user message
        await save_message_async(session_id=session_id, role="user", content=body.query)

        # Load recent history
        history = await list_messages_async(session_id)
        history = [m for m in history if not (m["role"] == "user" and m["content"] == body.query)]

        yield _sse("ping", {"ts": 0})

        # Set up the keep-alive ticker. We use an asyncio.Queue to ferry
        # "tick" markers from a background task to the consumer; when a
        # tick arrives (or after a max idle window) the consumer emits
        # an SSE comment.
        stop = asyncio.Event()
        ticker_q: asyncio.Queue[str] = asyncio.Queue()

        async def _ticker() -> None:
            try:
                while not stop.is_set():
                    await asyncio.sleep(SSE_KEEPALIVE_INTERVAL)
                    if stop.is_set():
                        return
                    await ticker_q.put("tick")
            except asyncio.CancelledError:
                return

        ticker = asyncio.create_task(_ticker())

        final_text = ""
        tool_calls_log: list[dict[str, Any]] = []
        agent_iter = run_agent_stream(
            user_query=body.query, history=history, session_id=session_id
        )
        # Bridge: convert the async generator into a queue
        agent_q: asyncio.Queue[Any] = asyncio.Queue(maxsize=64)

        async def _pump_agent() -> None:
            try:
                async for ev in agent_iter:
                    await agent_q.put(ev)
            except Exception as exc:  # noqa: BLE001
                await agent_q.put(exc)
            finally:
                await agent_q.put(None)  # sentinel: agent done

        pump = asyncio.create_task(_pump_agent())

        try:
            while True:
                # If client disconnected, stop
                if await request.is_disconnected():
                    logger.info("client disconnected, aborting stream")
                    break

                # Wait for either: an agent event, a keep-alive tick, or a small idle window
                # (so we re-check request.is_disconnected() periodically).
                queue_wait: asyncio.Task[Any] = asyncio.create_task(agent_q.get())
                tick_wait: asyncio.Task[Any] = asyncio.create_task(ticker_q.get())
                disconnect_tick: asyncio.Task[Any] = asyncio.create_task(
                    asyncio.sleep(1.0)
                )
                done, _pending = await asyncio.wait(
                    {queue_wait, tick_wait, disconnect_tick},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in _pending:
                    t.cancel()

                if queue_wait in done:
                    ev = queue_wait.result()
                    if ev is None:
                        # Agent finished
                        break
                    if isinstance(ev, Exception):
                        raise ev
                    event_name = ev.get("event", "")
                    event_data = ev.get("data", {})
                    yield _sse(event_name, event_data)
                    if event_name == "tool_result":
                        tool_calls_log.append(event_data)
                    if event_name == "message_final":
                        final_text = event_data.get("content", "")
                elif tick_wait in done:
                    # Keep-alive tick: emit a no-op SSE comment
                    yield _sse_keepalive()
                # else: just a disconnect-check tick; loop again
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream failed")
            yield _sse("error", {"message": str(exc)})
        finally:
            stop.set()
            for t in (ticker, pump):
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

        if final_text:
            await save_message_async(
                session_id=session_id,
                role="assistant",
                content=final_text,
                tool_calls=tool_calls_log or None,
                thinking={"trace": tool_calls_log},
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
