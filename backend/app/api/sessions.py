"""Session and message history endpoints (all async to coexist with FastAPI's event loop)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.security import AuthContext, require_user
from app.storage.repository import (
    create_session_async,
    delete_all_sessions_async,
    delete_session_for_user_async,
    get_session_for_user_async,
    list_messages_async,
    list_sessions_async,
    save_message_async,
    update_session_title_for_user_async,
)

router = APIRouter()


class SessionCreate(BaseModel):
    title: str = "新对话"


class SessionPatch(BaseModel):
    title: str | None = None


class MessageCreate(BaseModel):
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    thinking: dict[str, Any] | None = None


@router.post("/sessions")
async def post_session(body: SessionCreate, auth: AuthContext = Depends(require_user)) -> dict[str, Any]:
    sid = await create_session_async(title=body.title, user_id=auth.user_id)
    return {"id": sid, "title": body.title, "created_at": datetime.now(timezone.utc).isoformat()}


@router.get("/sessions")
async def get_sessions(
    limit: int = 50, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    return {"sessions": await list_sessions_async(user_id=auth.user_id, limit=min(max(limit, 1), 100))}


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    sess = await get_session_for_user_async(session_id, auth.user_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    return {"session": sess, "messages": await list_messages_async(session_id)}


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str, body: SessionPatch, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    if body.title is not None and not await update_session_title_for_user_async(
        session_id, auth.user_id, body.title
    ):
        raise HTTPException(404, "Session not found")
    if body.title is None and not await get_session_for_user_async(session_id, auth.user_id):
        raise HTTPException(404, "Session not found")
    return {"id": session_id, "title": body.title}


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(
    session_id: str, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    if not await delete_session_for_user_async(session_id, auth.user_id):
        raise HTTPException(404, "Session not found")
    return {"id": session_id, "deleted": True}


@router.delete("/sessions")
async def delete_all_sessions_endpoint(auth: AuthContext = Depends(require_user)) -> dict[str, Any]:
    """Wipe every session for this user. Returns the count of deleted rows."""
    count = await delete_all_sessions_async(user_id=auth.user_id)
    return {"deleted": count}


@router.post("/sessions/{session_id}/messages")
async def post_message(
    session_id: str, body: MessageCreate, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    if not await get_session_for_user_async(session_id, auth.user_id):
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
