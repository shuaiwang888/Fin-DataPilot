"""Pydantic-settings configuration. Single source of truth for all env-driven config."""
from __future__ import annotations

import os
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

    # ===== User-uploaded skills =====
    # Skills installed at runtime via POST /api/skills/upload live under
    # this directory. Must be on a path that survives HF Space rebuilds
    # (see user_skills_dir property below).
    local_user_skills_path: str = "./data/user_skills"
    # Hard cap on the size of an uploaded skill zip (after extraction).
    # Protects against zip bombs.
    max_skill_upload_bytes: int = 20 * 1024 * 1024  # 20 MB

    # ===== AnySearch (self-hosted web/vertical search skill) =====
    # Path to the unpacked anysearch-skill/ directory. The backend
    # shells out to <dir>/scripts/anysearch_cli.py (Python) — that CLI
    # reads .env + runtime.conf from this directory on its own.
    # Override to point at a different install location; default
    # resolves relative to the project root (../../Skills/anysearch-skill
    # from the backend/ working dir).
    anysearch_skill_dir: str = ""
    anysearch_timeout: int = 30  # seconds; CLI subprocess timeout
    anysearch_api_key: str = ""  # optional; if empty, anonymous (lower rate limits)

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
    def is_hf_space(self) -> bool:
        """True when we appear to be running on a HuggingFace Space.

        Detected by either the SPACE_ID env (set by the HF runtime on
        every Space) or by /data already existing and being writable —
        because /data is the canonical mount point for HF Space
        persistent storage.
        """
        if os.environ.get("SPACE_ID"):
            return True
        try:
            return Path("/data").is_dir() and os.access("/data", os.W_OK)
        except OSError:
            return False

    @property
    def persistent_db_path(self) -> str:
        """Where the SQLite file should live on disk.

        Resolution order:
          1. If `turso_database_url` is set → that's remote, this
             property is irrelevant (engine is built from `database_url`
             which prefers Turso).
          2. If we're on HF Space → MUST be /data/findatapilot.db.
             We probe writability up front and raise if it fails, so a
             misconfigured Space is loud, not silent.
          3. Otherwise → the configured local path (./data/...).

        Note: HF Space persistent storage must be enabled in the Space's
        Settings page, otherwise /data is wiped on every rebuild just
        like any other container path. The startup log
        (`Database tables ready at ...`) prints the resolved path —
        check it matches `/data/...` on HF.
        """
        if self.is_hf_space:
            hf_db = Path("/data/findatapilot.db")
            try:
                hf_db.parent.mkdir(parents=True, exist_ok=True)
                # Real write test — mkdir alone succeeds even on
                # non-persistent /data, but open(O_CREAT) doesn't.
                with open(hf_db, "ab") as _f:
                    pass
                return str(hf_db)
            except OSError as exc:
                # Loud failure: better to crash on startup than lose
                # the user's history on the next restart.
                raise RuntimeError(
                    f"/data is not writable on this HF Space (HF persistent "
                    f"storage must be enabled in the Space's Settings). "
                    f"Underlying error: {exc}"
                ) from exc
        return self.local_sqlite_path

    @property
    def user_skills_dir(self) -> str:
        """Directory for user-uploaded skills. Must persist across HF
        Space rebuilds, so we use /data/user_skills on HF Space and
        ./data/user_skills locally. Directory is created on first access.
        """
        if self.is_hf_space:
            d = Path("/data/user_skills")
        else:
            d = Path(self.local_user_skills_path)
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

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
    def anysearch_dir(self) -> str:
        """Resolve the on-disk path of the bundled anysearch-skill/.

        Resolution order:
          1. `anysearch_skill_dir` env (any-search-skill-path=…) if set
             and exists — use it as-is.
          2. <project_root>/Skills/anysearch-skill — the canonical
             install location tracked in the repo.
          3. <cwd>/Skills/anysearch-skill — fallback when the backend
             is started from the project root.

        Returns "" if no candidate exists (the skill should then refuse
        to register / dispatch with a clear error).
        """
        if self.anysearch_skill_dir and Path(self.anysearch_skill_dir).is_dir():
            return str(Path(self.anysearch_skill_dir).resolve())
        # backend/ lives at <project_root>/backend; the skill is at
        # <project_root>/Skills/anysearch-skill. So go up one level.
        for candidate in (
            Path(__file__).resolve().parents[2] / "Skills" / "anysearch-skill",
            Path.cwd() / "Skills" / "anysearch-skill",
        ):
            if candidate.is_dir():
                return str(candidate.resolve())
        return ""

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
