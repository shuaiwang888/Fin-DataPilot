"""DB initialization entrypoint — run on app startup to create tables."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.storage.db import Base, engine
from app.storage.models import Message, Session, SkillPref, ToolRun  # noqa: F401
from app.config import get_settings

logger = logging.getLogger(__name__)


async def init_db() -> None:
    settings = get_settings()
    # On HuggingFace Spaces, the project root is wiped on every
    # container rebuild, but /data persists. Migrate any pre-existing
    # local DB (e.g. from before the persistent path was wired) to
    # /data so the user doesn't lose history.
    if settings.turso_database_url:
        pass  # Turso / libSQL: nothing to migrate locally
    else:
        new_path = Path(settings.persistent_db_path)
        old_path = Path(settings.local_sqlite_path)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        if old_path.exists() and not new_path.exists() and new_path.parent != old_path.parent:
            try:
                shutil.copy2(old_path, new_path)
                logger.info("Migrated local SQLite to persistent path: %s", new_path)
            except OSError as exc:
                logger.warning("Could not migrate SQLite: %s", exc)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready at %s", settings.database_url)
