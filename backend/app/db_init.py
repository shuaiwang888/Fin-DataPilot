"""DB initialization entrypoint — run on app startup to create tables."""
from __future__ import annotations

import logging

from app.storage.db import Base, engine
from app.storage.models import Message, Session, SkillPref, ToolRun  # noqa: F401

logger = logging.getLogger(__name__)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")
