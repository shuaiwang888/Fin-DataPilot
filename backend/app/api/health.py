"""Health and readiness endpoints."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request

from app import __version__
from app.config import get_settings
from app.skills.registry import REGISTRY

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    settings = get_settings()
    return {
        "ok": True,
        "version": __version__,
        "env": settings.data_pilot_env,
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "api_key_configured": settings.has_real_llm_key,
        },
        "iwencai_key_configured": settings.has_iwencai_key,
        "agent": {
            "enabled": True,
            "max_reflect_rounds": settings.agent_max_reflect_rounds,
            "max_parallel_skills": settings.agent_max_parallel_skills,
        },
        "tools": {
            "count": len(REGISTRY.list_specs()),
            "names": [s.name for s in REGISTRY.list_specs()],
        },
    }


@router.get("/diag")
async def diag() -> dict:
    """Runtime DB diagnostics. Use this to verify the SQLite file is
    actually on the persistent volume.

    Returns the resolved DB path, whether /data is mounted as a
    separate filesystem (the only way to confirm HF persistent
    storage is working), and the file size.
    """
    settings = get_settings()
    info: dict = {
        "is_hf_space": settings.is_hf_space,
        "space_id": os.environ.get("SPACE_ID"),
        "turso_configured": bool(settings.turso_database_url),
    }
    if settings.turso_database_url:
        info["database_url"] = settings.database_url
    else:
        path = Path(settings.persistent_db_path)
        info["db_path"] = str(path)
        info["db_exists"] = path.exists()
        info["db_size_bytes"] = path.stat().st_size if path.exists() else 0
        info["data_dir_is_separate_mount"] = _is_separate_mount(str(path.parent))
        info["data_dir_mount_info"] = _mount_info(str(path.parent))
        # Don't construct the full SQLAlchemy URL just to log it — that
        # triggers a mkdir() side effect we don't need in a read-only
        # diagnostic. Reconstruct it locally instead.
        info["database_url"] = f"sqlite+aiosqlite:///{path}"
    return info


def _is_separate_mount(path: str) -> bool:
    """True when `path` lives on a different filesystem from /.

    On HF Space, /data is a persistent volume with its own device id;
    on a non-persistent container, /data is just a regular directory on
    the root filesystem. This is the most reliable runtime check.
    """
    try:
        return os.stat(path).st_dev != os.stat("/").st_dev
    except OSError:
        return False


def _mount_info(path: str) -> str:
    """Best-effort: parse /proc/mounts to describe how `path` is mounted."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == path.rstrip("/"):
                    return f"device={parts[0]} fs={parts[2]} opts={parts[3]}"
    except OSError:
        pass
    return "not in /proc/mounts"


@router.get("/echo")
async def echo(text: str) -> dict:
    """Round-trip echo endpoint to verify CORS without invoking the LLM."""
    return {"echo": text}
