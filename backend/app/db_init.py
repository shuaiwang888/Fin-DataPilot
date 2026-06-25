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
    # Diagnostic block: log WHERE the DB will live and whether we're
    # actually on a persistent volume. If you see "WARNING" below,
    # /data is NOT persisted and the user's history will be wiped on
    # the next Space restart. Fix: Space Settings → Persistent Storage.
    if settings.turso_database_url:
        logger.info(
            "DB backend: Turso (remote libSQL). Persistent across rebuilds."
        )
    elif settings.is_hf_space:
        db_path = Path(settings.persistent_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        is_real_volume = _is_persistent_mount("/data")
        if not is_real_volume:
            logger.warning(
                "DB backend: HF Space detected, but /data is NOT a persistent "
                "mount. History will be lost on every restart. → Fix: open the "
                "Space's Settings tab and enable 'Persistent Storage'."
            )
        logger.info(
            "DB backend: SQLite at %s (HF Space, persistent=%s)",
            db_path,
            is_real_volume,
        )
    else:
        logger.info(
            "DB backend: SQLite at %s (local dev)", settings.local_sqlite_path
        )

    # On HuggingFace Spaces, the project root is wiped on every
    # container rebuild, but /data persists. Migrate any pre-existing
    # local DB (e.g. from before the persistent path was wired) to
    # /data so the user doesn't lose history.
    if not settings.turso_database_url:
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


def _is_persistent_mount(path: str) -> bool:
    """Heuristic: is `path` mounted on a separate filesystem from /?

    On HF Space, the persistent /data volume is its own mount, so
    `os.stat(path).st_dev` differs from `os.stat('/').st_dev`. If it
    matches, /data is just a directory on the container's root
    filesystem and will be wiped on every rebuild.
    """
    import os
    try:
        return os.stat(path).st_dev != os.stat("/").st_dev
    except OSError:
        return False
