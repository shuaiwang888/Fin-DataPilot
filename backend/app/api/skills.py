"""Skills management endpoints."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.skills.registry import REGISTRY

router = APIRouter()


def _is_env_configured(name: str) -> bool:
    """Return True if the env var is present and not the placeholder value."""
    if not name:
        return True
    val = os.environ.get(name, "")
    if not val:
        return False
    # Treat any "your-X-key-here" placeholder as missing
    if val.startswith("your-") and val.endswith("-here"):
        return False
    return True


@router.get("/skills")
async def list_skills() -> dict:
    """List all registered skills, with enabled/disabled state and runtime env status."""
    return {
        "skills": [
            {
                "spec": s.model_dump(),
                "enabled": REGISTRY.is_enabled(s.name),
                "requirements_met": {
                    env: _is_env_configured(env) for env in s.requires
                },
            }
            for s in REGISTRY.list_specs()
        ]
    }


class SkillToggleRequest(BaseModel):
    enabled: bool


@router.patch("/skills/{name}")
async def toggle_skill(name: str, body: SkillToggleRequest) -> dict:
    if not REGISTRY.get_spec(name):
        raise HTTPException(404, f"Unknown skill '{name}'")
    REGISTRY.set_enabled(name, body.enabled)
    return {"name": name, "enabled": body.enabled}


class SkillDebugRequest(BaseModel):
    args: dict = {}


@router.post("/skills/{name}/debug")
async def debug_skill(name: str, body: SkillDebugRequest) -> dict:
    """Manually invoke a skill (bypassing the LLM). Useful for testing."""
    from app.skills.registry import REGISTRY as R

    if not R.get_spec(name):
        raise HTTPException(404, f"Unknown skill '{name}'")
    if not R.is_enabled(name):
        raise HTTPException(400, f"Skill '{name}' is disabled")
    result = await R.dispatch(name, body.args)
    return result.to_dict()
