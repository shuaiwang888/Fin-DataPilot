"""Shared test isolation for the database-backed API and repository tests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest_asyncio

# Tests must never depend on a developer's existing ./data database (which can
# hide missing migrations) or accidentally connect to a configured Turso DB.
_TEST_DB_PATH = Path(tempfile.gettempdir()) / f"findatapilot-pytest-{os.getpid()}.db"
os.environ["LOCAL_SQLITE_PATH"] = str(_TEST_DB_PATH)
os.environ["TURSO_DATABASE_URL"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""
os.environ.pop("SPACE_ID", None)


@pytest_asyncio.fixture(autouse=True)
async def _isolated_database_schema(request):
    """Create a fresh schema for each test, then remove every table.

    The engine is imported only after the test-only environment variables are
    installed above. Importing models is required before ``create_all`` so all
    mapped tables are registered on SQLAlchemy's metadata.
    """
    if request.node.get_closest_marker("database") is None:
        yield
        return

    from app.storage import models  # noqa: F401
    from app.storage.db import Base, engine

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
