"""User-facing controls for inspectable, erasable long-term memory."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.config import get_settings
from app.security import AuthContext, require_user
from app.storage.repository import (
    clear_memories_for_user_async,
    delete_long_term_memory_for_user_async,
    list_long_term_memories_async,
)

router = APIRouter()


@router.get("/memories")
async def get_memories(auth: AuthContext = Depends(require_user)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": settings.memory_enabled,
        "identity_scope": "this_browser",
        "memories": await list_long_term_memories_async(auth.user_id),
    }


@router.delete("/memories")
async def clear_memories(auth: AuthContext = Depends(require_user)) -> dict[str, Any]:
    deleted = await clear_memories_for_user_async(auth.user_id)
    return {"deleted": deleted}


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str, auth: AuthContext = Depends(require_user)
) -> dict[str, Any]:
    if not await delete_long_term_memory_for_user_async(memory_id, auth.user_id):
        raise HTTPException(404, "Memory not found")
    return {"id": memory_id, "deleted": True}
