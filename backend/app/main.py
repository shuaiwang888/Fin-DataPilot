"""Fin-DataPilot FastAPI application entrypoint.

Triggered by the user adding HF_SSH_PRIVATE_KEY to GitHub Secrets.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app import __version__
from app.api import agent, auth, health, memories, sessions, skills
from app.config import get_settings
from app.db_init import init_db
from app.skills import registry as _skills_registry  # noqa: F401 — trigger registration
from app.skills.configuration import refresh_published_skill_configuration
from app.skills.user_uploads import load_uploaded_skills_at_startup
from app.utils.trace import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    await init_db()
    # After the 4 built-in skills have registered, re-import any
    # previously uploaded skills so they survive container restarts.
    n_loaded = load_uploaded_skills_at_startup()
    if n_loaded:
        logger.info(
            "Loaded %d uploaded skill(s) from %s",
            n_loaded,
            settings.user_skills_dir,
        )
    await refresh_published_skill_configuration()
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Fin-DataPilot",
        version=__version__,
        description="Natural-language financial data agent platform",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    # Fail loudly before serving any request with an unsigned identity in
    # production. Accessing the property performs the environment check.
    _ = settings.effective_auth_secret

    # CORS: credentials are not used (the API uses bearer headers), so don't
    # opt browsers into cross-origin cookie semantics.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    # Rate limiting (per remote IP)
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    # Routers
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(skills.router, prefix="/api", tags=["skills"])
    app.include_router(sessions.router, prefix="/api", tags=["sessions"])
    app.include_router(memories.router, prefix="/api", tags=["memories"])
    app.include_router(agent.router, prefix="/api/agent", tags=["agent"])

    return app


app = create_app()
