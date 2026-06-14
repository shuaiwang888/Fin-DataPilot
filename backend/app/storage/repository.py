"""Repository functions over the storage models. Pure async + SQLAlchemy 2.0 style."""
from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.db import SessionLocal
from app.storage.models import Message, Session, SkillPref, ToolRun


def _new_id() -> str:
    return secrets.token_urlsafe(16)


# ---------- sessions ----------

async def create_session_async(title: str, user_id: str) -> str:
    sid = _new_id()
    async with SessionLocal() as db:
        db.add(Session(id=sid, title=title, user_id=user_id))
        await db.commit()
    return sid


def create_session(title: str, user_id: str = "default") -> str:
    import asyncio

    return asyncio.run(create_session_async(title, user_id))


async def list_sessions_async(user_id: str, limit: int) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        result = await db.execute(
            select(Session).where(Session.user_id == user_id).order_by(Session.updated_at.desc()).limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in rows
        ]


def list_sessions(user_id: str = "default", limit: int = 50) -> list[dict[str, Any]]:
    import asyncio

    return asyncio.run(list_sessions_async(user_id, limit))


async def get_session_async(session_id: str) -> dict[str, Any] | None:
    async with SessionLocal() as db:
        s = await db.get(Session, session_id)
        if not s:
            return None
        return {
            "id": s.id,
            "title": s.title,
            "user_id": s.user_id,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }


def get_session(session_id: str) -> dict[str, Any] | None:
    import asyncio

    return asyncio.run(get_session_async(session_id))


async def update_session_title_async(session_id: str, title: str) -> None:
    async with SessionLocal() as db:
        s = await db.get(Session, session_id)
        if s:
            s.title = title
            await db.commit()


def update_session_title(session_id: str, title: str) -> None:
    import asyncio

    asyncio.run(update_session_title_async(session_id, title))


async def delete_session_async(session_id: str) -> None:
    async with SessionLocal() as db:
        s = await db.get(Session, session_id)
        if s:
            await db.delete(s)
            await db.commit()


def delete_session(session_id: str) -> None:
    import asyncio

    asyncio.run(delete_session_async(session_id))


# ---------- messages ----------

async def save_message_async(
    session_id: str,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    thinking: dict[str, Any] | None = None,
) -> str:
    import json

    mid = _new_id()
    async with SessionLocal() as db:
        db.add(
            Message(
                id=mid,
                session_id=session_id,
                role=role,
                content=content,
                tool_calls_json=json.dumps(tool_calls) if tool_calls else None,
                tool_call_id=tool_call_id,
                thinking_json=json.dumps(thinking) if thinking else None,
            )
        )
        # bump session updated_at
        s = await db.get(Session, session_id)
        if s:
            from datetime import datetime, timezone

            s.updated_at = datetime.now(timezone.utc)
        await db.commit()
    return mid


def save_message(
    session_id: str,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    thinking: dict[str, Any] | None = None,
) -> str:
    import asyncio

    return asyncio.run(
        save_message_async(
            session_id, role, content, tool_calls, tool_call_id, thinking
        )
    )


async def list_messages_async(session_id: str) -> list[dict[str, Any]]:
    import json

    async with SessionLocal() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        rows = result.scalars().all()
        out: list[dict[str, Any]] = []
        for m in rows:
            entry: dict[str, Any] = {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            if m.tool_calls_json:
                entry["tool_calls"] = json.loads(m.tool_calls_json)
            if m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            if m.thinking_json:
                entry["thinking"] = json.loads(m.thinking_json)
            out.append(entry)
        return out


def list_messages(session_id: str) -> list[dict[str, Any]]:
    import asyncio

    return asyncio.run(list_messages_async(session_id))
