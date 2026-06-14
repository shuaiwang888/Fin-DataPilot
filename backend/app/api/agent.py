"""Agent chat streaming endpoint (SSE)."""
from __future__ import annotations

import json
import logging
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


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None
    user_id: str = "default"


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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

        final_text = ""
        tool_calls_log: list[dict[str, Any]] = []
        try:
            async for ev in run_agent_stream(
                user_query=body.query, history=history, session_id=session_id
            ):
                event_name = ev.get("event", "")
                event_data = ev.get("data", {})
                yield _sse(event_name, event_data)

                if event_name == "tool_result":
                    tool_calls_log.append(event_data)
                if event_name == "message_final":
                    final_text = event_data.get("content", "")

                if await request.is_disconnected():
                    logger.info("client disconnected, aborting stream")
                    break
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream failed")
            yield _sse("error", {"message": str(exc)})

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
