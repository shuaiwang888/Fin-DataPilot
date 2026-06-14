"""Test the per-user session retention cap."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage import repository
from app.storage.models import Message, Session


@pytest.mark.asyncio
async def test_retention_caps_at_50(monkeypatch):
    """Creating more than 50 sessions should prune the oldest ones."""
    # Force a small cap for fast tests. Patch where it's looked up
    # (app.storage.repository), not where it's defined, because
    # `from app.config import get_settings` makes a local binding.
    class FakeSettings:
        max_sessions_per_user = 5
        turso_database_url = ""
        turso_auth_token = ""
        local_sqlite_path = "./data/findatapilot.db"
    monkeypatch.setattr("app.storage.repository.get_settings", lambda: FakeSettings())

    from app.storage.db import engine
    from sqlalchemy import delete

    # Clean slate
    async with engine.begin() as conn:
        await conn.run_sync(lambda m: None)  # no-op
    async with engine.begin() as conn:
        await conn.execute(delete(Message))
        await conn.execute(delete(Session))

    # Create 7 sessions
    sids = [await repository.create_session_async(title=f"t{i}", user_id="test") for i in range(7)]

    # Verify only 5 remain (cap)
    remaining = await repository.list_sessions_async(user_id="test", limit=100)
    titles = [s["title"] for s in remaining]
    assert len(remaining) == 5, f"expected 5, got {len(remaining)}: {titles}"
    # The 5 kept should be the LAST 5 created (t2..t6), since we
    # delete the oldest first.
    assert "t2" in titles and "t6" in titles
    assert "t0" not in titles and "t1" not in titles


@pytest.mark.asyncio
async def test_retention_disabled_when_cap_is_zero(monkeypatch):
    """Setting max_sessions_per_user=0 disables the cap."""
    class FakeSettings:
        max_sessions_per_user = 0
        turso_database_url = ""
        turso_auth_token = ""
        local_sqlite_path = "./data/findatapilot.db"
    monkeypatch.setattr("app.storage.repository.get_settings", lambda: FakeSettings())

    from sqlalchemy import delete
    from app.storage.db import engine

    async with engine.begin() as conn:
        await conn.execute(delete(Message))
        await conn.execute(delete(Session))

    sids = [await repository.create_session_async(title=f"x{i}", user_id="test2") for i in range(8)]
    remaining = await repository.list_sessions_async(user_id="test2", limit=100)
    assert len(remaining) == 8
