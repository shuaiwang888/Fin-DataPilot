"""Pydantic-settings configuration. Single source of truth for all env-driven config."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment + .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== LLM =====
    llm_provider: Literal["minimax", "openai", "anthropic", "custom"] = "minimax"
    llm_base_url: str = "https://api.minimaxi.com/v1"
    llm_api_key: str = "your-api-key-here"
    llm_model: str = "MiniMax-M3"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096

    # ===== iWencai (used by 4 skills via X-Claw-* headers) =====
    iwencai_api_key: str = "your-iwencai-key-here"
    iwencai_skill_id_overrides: str = ""  # e.g. "financial-query=hithink-financial-query"

    # ===== Server =====
    data_pilot_host: str = "0.0.0.0"
    data_pilot_port: int = 7860
    data_pilot_env: Literal["development", "staging", "production"] = "development"
    api_key: str = ""  # if set, clients must send X-API-Key header

    # ===== CORS =====
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"

    # ===== Storage =====
    turso_database_url: str = ""
    turso_auth_token: str = ""
    local_sqlite_path: str = "./data/findatapilot.db"

    # ===== Session retention =====
    # Per-user cap on stored sessions. When a new session is created
    # and the user already has this many, the OLDEST session (by
    # created_at) is deleted to make room. Set to 0 to disable.
    max_sessions_per_user: int = 50

    # ===== Agent =====
    agent_max_reflect_rounds: int = 5
    agent_max_parallel_skills: int = 3
    agent_enable_reflection: bool = True

    # ===== Observability =====
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    log_level: str = "INFO"

    # ===== Derived =====
    @field_validator("cors_allow_origins")
    @classmethod
    def _strip_cors(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        if self.turso_database_url:
            token = f"?token={self.turso_auth_token}" if self.turso_auth_token else ""
            return f"sqlite+aiosqlite://{self.turso_database_url}{token}"
        # On HF Space, prefer the persistent /data path; otherwise use
        # the configured local path. Both are persistent for the
        # container's lifetime, but /data survives rebuilds.
        path = self.persistent_db_path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path}"

    @property
    def persistent_db_path(self) -> str:
        """Path used on HuggingFace Spaces. The /data directory persists
        across restarts and rebuilds, unlike the project root which
        gets wiped on every container rebuild.

        Falls back to the configured local path if /data isn't writable.
        """
        # On HF Space, /data is the only path that survives rebuilds.
        hf_data = Path("/data/findatapilot.db")
        try:
            hf_data.parent.mkdir(parents=True, exist_ok=True)
            return str(hf_data)
        except OSError:
            return self.local_sqlite_path

    @property
    def iwencai_skill_id_map(self) -> dict[str, str]:
        """Map local skill name → iWencai X-Claw-Skill-Id (platform registration name).

        Note: financial-query (local, general-purpose) shares identity with
        `hithink-astock-selector` on the iWencai platform — the gateway only accepts
        that exact value. Override via IWENCAI_SKILL_ID_OVERRIDES env if needed.
        """
        out: dict[str, str] = {
            "financial-query": "hithink-astock-selector",  # platform registration name
            "news-search": "news-search",
            "announcement-search": "announcement-search",
            "report-search": "report-search",
        }
        if not self.iwencai_skill_id_overrides.strip():
            return out
        for pair in self.iwencai_skill_id_overrides.split(","):
            if "=" in pair:
                local, platform = pair.split("=", 1)
                out[local.strip()] = platform.strip()
        return out

    @property
    def is_production(self) -> bool:
        return self.data_pilot_env == "production"

    @property
    def has_real_llm_key(self) -> bool:
        return self.llm_api_key not in ("", "your-api-key-here")

    @property
    def has_iwencai_key(self) -> bool:
        return self.iwencai_api_key not in ("", "your-iwencai-key-here")


# Singleton accessor (read env once at import time)
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
