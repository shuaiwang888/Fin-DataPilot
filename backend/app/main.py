"""Fin-DataPilot FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app import __version__
from app.api import agent, health, sessions, skills
from app.config import get_settings
from app.db_init import init_db
from app.skills import registry as _skills_registry  # noqa: F401 — trigger registration
from app.utils.trace import setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    await init_db()
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

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    # Rate limiting (per remote IP)
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    # Optional API key enforcement
    if settings.api_key:

        @app.middleware("http")
        async def api_key_guard(request: Request, call_next):
            if request.url.path in {"/api/health", "/docs", "/openapi.json", "/redoc"}:
                return await call_next(request)
            provided = request.headers.get("X-API-Key", "")
            if provided != settings.api_key:
                return ORJSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    # Routers
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(skills.router, prefix="/api", tags=["skills"])
    app.include_router(sessions.router, prefix="/api", tags=["sessions"])
    app.include_router(agent.router, prefix="/api/agent", tags=["agent"])

    return app


app = create_app()
