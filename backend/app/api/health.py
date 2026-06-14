"""Health and readiness endpoints."""
from __future__ import annotations

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


@router.get("/echo")
async def echo(text: str) -> dict:
    """Round-trip echo endpoint to verify CORS without invoking the LLM."""
    return {"echo": text}
