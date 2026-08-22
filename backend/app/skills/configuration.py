"""Database-backed published Skill configuration for all application replicas."""
from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from app.skills.registry import REGISTRY
from app.storage.repository import (
    list_published_skill_preferences_async,
    set_published_skill_preference_async,
)

logger = logging.getLogger(__name__)


async def refresh_published_skill_configuration() -> None:
    """Reload the small shared catalog at request/dispatch boundaries."""
    try:
        preferences = await list_published_skill_preferences_async()
    except SQLAlchemyError:
        # Startup / test-schema failure must not make an already registered
        # catalog unusable. The next request refreshes it after DB recovery.
        logger.warning("published Skill configuration unavailable; retaining in-memory catalog", exc_info=True)
        return
    for name, enabled in preferences.items():
        if REGISTRY.get_spec(name) is not None:
            REGISTRY.set_enabled(name, enabled)


async def set_published_skill_configuration(name: str, enabled: bool) -> None:
    if REGISTRY.get_spec(name) is None:
        raise KeyError(name)
    await set_published_skill_preference_async(name, enabled)
    REGISTRY.set_enabled(name, enabled)
