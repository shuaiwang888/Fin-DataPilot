"""Skills management endpoints."""
import os

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.security import AuthContext, require_admin, require_user
from app.skills import user_uploads
from app.skills.registry import REGISTRY

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


def _is_env_configured(name: str) -> bool:
    """Return True if the env var is present and not the placeholder value."""
    if not name:
        return True
    val = os.environ.get(name, "")
    if not val:
        return False
    # Treat any "your-X-key-here" placeholder as missing
    return not (val.startswith("your-") and val.endswith("-here"))


@router.get("/skills")
async def list_skills(auth: AuthContext = Depends(require_user)) -> dict[str, object]:
    """List all registered skills, with enabled/disabled state, runtime
    env status, an `uploaded` flag distinguishing user-uploaded skills
    (which can be deleted) from built-ins (which cannot), and a `kind`
    field ("code" / "prompt" / "builtin") so the UI can render
    prompt-only skills differently."""
    settings = get_settings()
    user_root = settings.user_skills_dir
    _ = auth
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
@limiter.limit("30/minute")
async def toggle_skill(
    name: str,
    body: SkillToggleRequest,
    request: Request,
    admin: AuthContext = Depends(require_admin),
) -> dict[str, object]:
    _ = admin
    if not REGISTRY.get_spec(name):
        raise HTTPException(404, f"Unknown skill '{name}'")
    REGISTRY.set_enabled(name, body.enabled)
    return {"name": name, "enabled": body.enabled}


class SkillDebugRequest(BaseModel):
    args: dict[str, object] = Field(default_factory=dict)


@router.post("/skills/{name}/debug")
@limiter.limit("20/minute")
async def debug_skill(
    name: str,
    body: SkillDebugRequest,
    request: Request,
    admin: AuthContext = Depends(require_admin),
) -> dict[str, object]:
    """Manually invoke a skill (bypassing the LLM). Useful for testing."""
    from app.skills.registry import REGISTRY as R

    if not R.get_spec(name):
        raise HTTPException(404, f"Unknown skill '{name}'")
    if not R.is_enabled(name):
        raise HTTPException(400, f"Skill '{name}' is disabled")
    _ = admin
    result = await R.dispatch(name, body.args)
    return result.to_dict()


@router.post("/skills/upload")
@limiter.limit("5/hour")
async def upload_skill(
    request: Request,
    file: UploadFile = File(...),
    admin: AuthContext = Depends(require_admin),
) -> dict[str, object]:
    """Upload a new skill as a zip file. See backend/app/skills/user_uploads.py
    for the expected zip layout (one top-level directory containing
    SKILL.md and a handler module)."""
    settings = get_settings()
    _ = admin
    if not settings.enable_skill_upload:
        raise HTTPException(403, "Skill upload is disabled; enable it only in an isolated admin environment")
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
            raise HTTPException(409, msg) from e
        raise HTTPException(400, msg) from e


@router.delete("/skills/{name}")
@limiter.limit("10/minute")
async def delete_skill(
    name: str,
    request: Request,
    admin: AuthContext = Depends(require_admin),
) -> dict[str, object]:
    """Delete an uploaded skill. Built-in skills cannot be deleted."""
    _ = admin
    try:
        user_uploads.uninstall_skill(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"deleted": name}
