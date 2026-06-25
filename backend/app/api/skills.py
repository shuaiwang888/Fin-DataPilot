"""Skills management endpoints."""
from __future__ import annotations

import os

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import get_settings
from app.skills import user_uploads
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
    """List all registered skills, with enabled/disabled state, runtime
    env status, an `uploaded` flag distinguishing user-uploaded skills
    (which can be deleted) from built-ins (which cannot), and a `kind`
    field ("code" / "prompt" / "builtin") so the UI can render
    prompt-only skills differently."""
    settings = get_settings()
    user_root = settings.user_skills_dir
    return {
        "skills": [
            {
                "spec": s.model_dump(),
                "enabled": REGISTRY.is_enabled(s.name),
                "requirements_met": {
                    env: _is_env_configured(env) for env in s.requires
                },
                "uploaded": os.path.isdir(os.path.join(user_root, s.name)),
                "kind": _classify_kind(s.name, user_root),
            }
            for s in REGISTRY.list_specs()
        ]
    }


def _classify_kind(name: str, user_root: str) -> str:
    """Return "builtin", "code" (uploaded with a .py), or "prompt"
    (uploaded with only SKILL.md)."""
    skill_dir = os.path.join(user_root, name)
    if not os.path.isdir(skill_dir):
        return "builtin"
    has_py = os.path.isfile(os.path.join(skill_dir, f"{name}.py"))
    return "code" if has_py else "prompt"


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


@router.post("/skills/upload")
async def upload_skill(file: UploadFile = File(...)) -> dict:
    """Upload a new skill as a zip file. See backend/app/skills/user_uploads.py
    for the expected zip layout (one top-level directory containing
    SKILL.md and a handler module)."""
    settings = get_settings()
    blob = await file.read()
    if not blob:
        raise HTTPException(400, "Empty upload")
    if len(blob) > settings.max_skill_upload_bytes:
        raise HTTPException(
            413,
            f"Upload exceeds {settings.max_skill_upload_bytes // (1024*1024)} MB limit",
        )
    try:
        return user_uploads.install_skill_from_zip(blob)
    except ValueError as e:
        # 409 for name conflicts (caller can rebrand), 400 for everything else
        msg = str(e)
        if "conflicts with a built-in" in msg or "already" in msg:
            raise HTTPException(409, msg)
        raise HTTPException(400, msg)


@router.delete("/skills/{name}")
async def delete_skill(name: str) -> dict:
    """Delete an uploaded skill. Built-in skills cannot be deleted."""
    try:
        user_uploads.uninstall_skill(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deleted": name}
