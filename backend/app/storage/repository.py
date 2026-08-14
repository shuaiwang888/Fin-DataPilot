"""Repository functions over the storage models. Pure async + SQLAlchemy 2.0 style."""
from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.storage.db import SessionLocal
from app.storage.models import AgentRun, Message, Session

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return secrets.token_urlsafe(16)


# ---------- sessions ----------

async def _prune_old_sessions(db: AsyncSession, user_id: str) -> int:
    """If the user has more than `max_sessions_per_user` sessions,
    delete the oldest by `created_at` until the count is at most
    that limit. Returns the number deleted. Messages cascade.

    `max_sessions_per_user == 0` disables the cap.
    """
    cap = get_settings().max_sessions_per_user
    if cap <= 0:
        return 0
    result = await db.execute(
        select(Session)
        .where(Session.user_id == user_id)
        .order_by(Session.created_at.desc())
    )
    rows = result.scalars().all()
    if len(rows) <= cap:
        return 0
    excess = [r.id for r in rows[cap:]]
    from sqlalchemy import delete

    await db.execute(delete(Session).where(Session.id.in_(excess)))
    return len(excess)


async def create_session_async(title: str, user_id: str) -> str:
    sid = _new_id()
    async with SessionLocal() as db:
        db.add(Session(id=sid, title=title, user_id=user_id))
        await db.commit()
        # Enforce retention: keep at most max_sessions_per_user
        pruned = await _prune_old_sessions(db, user_id)
        if pruned:
            await db.commit()
            logger.info("Pruned %d oldest session(s) for user=%s", pruned, user_id)
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


async def get_session_for_user_async(session_id: str, user_id: str) -> dict[str, Any] | None:
    """Return a session only when it belongs to the authenticated user."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            return None
        return {
            "id": s.id,
            "title": s.title,
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


async def update_session_title_for_user_async(session_id: str, user_id: str, title: str) -> bool:
    async with SessionLocal() as db:
        result = await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            return False
        session.title = title
        await db.commit()
        return True


def update_session_title(session_id: str, title: str) -> None:
    import asyncio

    asyncio.run(update_session_title_async(session_id, title))


async def delete_session_async(session_id: str) -> None:
    async with SessionLocal() as db:
        s = await db.get(Session, session_id)
        if s:
            await db.delete(s)
            await db.commit()


async def delete_session_for_user_async(session_id: str, user_id: str) -> bool:
    async with SessionLocal() as db:
        result = await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            return False
        await db.delete(session)
        await db.commit()
        return True


def delete_session(session_id: str) -> None:
    import asyncio

    asyncio.run(delete_session_async(session_id))


async def delete_all_sessions_async(user_id: str = "default") -> int:
    """Delete every session belonging to `user_id`. Returns the count."""
    from sqlalchemy import delete

    async with SessionLocal() as db:
        result = await db.execute(delete(Session).where(Session.user_id == user_id))
        await db.commit()
        return result.rowcount or 0


def delete_all_sessions(user_id: str = "default") -> int:
    import asyncio

    return asyncio.run(delete_all_sessions_async(user_id))


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


# ---------- durable agent runs ----------

async def create_agent_run_async(session_id: str, user_id: str, query: str) -> str:
    run_id = _new_id()
    async with SessionLocal() as db:
        db.add(AgentRun(id=run_id, session_id=session_id, user_id=user_id, query=query))
        await db.commit()
    return run_id


async def append_agent_run_event_async(run_id: str, event: dict[str, Any]) -> None:
    """Persist a bounded event trail. Never let one run grow without limit."""
    import json

    async with SessionLocal() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            return
        try:
            events = json.loads(run.events_json)
        except json.JSONDecodeError:
            events = []
        if not isinstance(events, list):
            events = []
        # Event payloads are intentionally compact; keep enough for an
        # interrupted UI to rebuild its trace while bounding SQLite growth.
        events.append(event)
        run.events_json = json.dumps(events[-128:], ensure_ascii=False)
        await db.commit()


async def finish_agent_run_async(
    run_id: str, status: str, final_text: str | None = None, error: str | None = None
) -> None:
    async with SessionLocal() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            return
        run.status = status
        run.final_text = final_text
        run.error = error
        await db.commit()


async def get_agent_run_for_user_async(run_id: str, user_id: str) -> dict[str, Any] | None:
    import json

    async with SessionLocal() as db:
        result = await db.execute(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return None
        try:
            events = json.loads(run.events_json)
        except json.JSONDecodeError:
            events = []
        return {
            "id": run.id,
            "session_id": run.session_id,
            "status": run.status,
            "events": events if isinstance(events, list) else [],
            "final_text": run.final_text,
            "error": run.error,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }


def list_messages(session_id: str) -> list[dict[str, Any]]:
    import asyncio

    return asyncio.run(list_messages_async(session_id))
