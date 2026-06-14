"""Skills management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.skills.registry import REGISTRY

router = APIRouter()


@router.get("/skills")
async def list_skills() -> dict:
    """List all registered skills, with their enabled/disabled state."""
    return {
        "skills": [
            {
                "spec": s.model_dump(),
                "enabled": REGISTRY.is_enabled(s.name),
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
