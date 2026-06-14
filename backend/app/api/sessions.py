"""Session and message history endpoints (all async to coexist with FastAPI's event loop)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.storage.repository import (
    create_session_async,
    delete_all_sessions,
    delete_all_sessions_async,
    delete_session_async,
    get_session_async,
    list_messages_async,
    list_sessions_async,
    save_message_async,
    update_session_title_async,
)

router = APIRouter()


class SessionCreate(BaseModel):
    title: str = "新对话"
    user_id: str = "default"


class SessionPatch(BaseModel):
    title: str | None = None


class MessageCreate(BaseModel):
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    thinking: dict[str, Any] | None = None


@router.post("/sessions")
async def post_session(body: SessionCreate) -> dict:
    sid = await create_session_async(title=body.title, user_id=body.user_id)
    return {"id": sid, "title": body.title, "created_at": datetime.now(timezone.utc).isoformat()}


@router.get("/sessions")
async def get_sessions(user_id: str = "default", limit: int = 50) -> dict:
    return {"sessions": await list_sessions_async(user_id=user_id, limit=limit)}


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str) -> dict:
    sess = await get_session_async(session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    return {"session": sess, "messages": await list_messages_async(session_id)}


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, body: SessionPatch) -> dict:
    if not await get_session_async(session_id):
        raise HTTPException(404, "Session not found")
    if body.title is not None:
        await update_session_title_async(session_id, body.title)
    return {"id": session_id, "title": body.title}


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str) -> dict:
    await delete_session_async(session_id)
    return {"id": session_id, "deleted": True}


@router.delete("/sessions")
async def delete_all_sessions_endpoint(user_id: str = "default") -> dict:
    """Wipe every session for this user. Returns the count of deleted rows."""
    count = await delete_all_sessions_async(user_id=user_id)
    return {"deleted": count, "user_id": user_id}


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageCreate) -> dict:
    if not await get_session_async(session_id):
        raise HTTPException(404, "Session not found")
    mid = await save_message_async(
        session_id=session_id,
        role=body.role,
        content=body.content,
        tool_calls=body.tool_calls,
        tool_call_id=body.tool_call_id,
        thinking=body.thinking,
    )
    return {"id": mid}
