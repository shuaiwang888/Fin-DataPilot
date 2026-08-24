"""Pydantic-settings configuration. Single source of truth for all env-driven config."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_writable_directory(path: Path) -> bool:
    """Create and probe a directory without leaving a test artifact behind."""
    probe = path / ".findatapilot-write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        with open(probe, "ab"):
            pass
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


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
    # ``api_key`` is retained as a backwards-compatible alias for operator
    # endpoints. It is never used as a browser-facing user identity.
    api_key: str = ""
    admin_api_key: str = ""
    auth_secret: str = ""
    # Anonymous browser identities are renewable before expiry. A long default
    # keeps cross-session memory useful without pretending to be account sync.
    auth_token_ttl_hours: int = 24 * 365

    # ===== CORS =====
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"

    # ===== Storage =====
    turso_database_url: str = ""
    turso_auth_token: str = ""
    local_sqlite_path: str = "./data/findatapilot.db"
    # Used only when an HF Space has no writable persistent /data volume.
    # The fallback keeps the service available but is erased on a restart.
    hf_ephemeral_data_path: str = "/tmp/findatapilot"

    # ===== User-uploaded skills =====
    # Skills installed at runtime via POST /api/skills/upload live under
    # this directory. Must be on a path that survives HF Space rebuilds
    # (see user_skills_dir property below).
    local_user_skills_path: str = "./data/user_skills"
    # Hard cap on the size of an uploaded skill zip (after extraction).
    # Protects against zip bombs.
    max_skill_upload_bytes: int = 20 * 1024 * 1024  # 20 MB
    # Code uploads execute Python and are therefore disabled by default even
    # for administrators. Prompt-only uploads are also treated as operator
    # content and use the same endpoint guard.
    enable_skill_upload: bool = False

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

    # ===== Memory =====
    memory_enabled: bool = True
    memory_short_term_ttl_days: int = 30
    memory_short_summary_max_chars: int = 4_000
    memory_long_term_max_items: int = 100
    memory_recall_max_items: int = 12

    # ===== Agent =====
    # Hard per-user-question cap. Every dispatch counts, including successful
    # calls, so a re-planning loop cannot make unbounded external requests.
    agent_max_skill_calls: int = 8
    # Replanning is deliberately separate from dispatches: each cycle can add
    # evidence-aware sub-tasks, while the dispatch ceiling remains the final
    # protection against unbounded external requests.
    agent_max_planning_cycles: int = 5
    agent_skill_resource_max_chars: int = 12_000
    # The graph is deliberately sequential while a later step can depend on
    # evidence from an earlier one. Kept for API compatibility only.
    agent_max_parallel_skills: int = 1
    agent_enable_reflection: bool = True
    agent_run_timeout_seconds: int = 600

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

    @field_validator("agent_max_skill_calls")
    @classmethod
    def _cap_agent_skill_calls(cls, v: int) -> int:
        """Keep the per-question dispatch budget within the product contract."""
        return min(max(v, 1), 8)

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
          2. If we're on HF Space → /data/findatapilot.db when that
             persistent volume is writable; otherwise a writable ephemeral
             directory, so a mount-permission change cannot take the API down.
          3. Otherwise → the configured local path (./data/...).

        The startup log prints the resolved path. On Hugging Face, configure
        persistent storage (or Turso) whenever user history must survive a
        restart; the fallback path intentionally does not promise durability.
        """
        if self.is_hf_space:
            return str(self.hf_storage_dir / "findatapilot.db")
        return self.local_sqlite_path

    @property
    def hf_storage_dir(self) -> Path:
        """Return writable HF storage, preferring the durable `/data` mount."""
        persistent = Path("/data")
        if _is_writable_directory(persistent):
            return persistent

        fallback = Path(self.hf_ephemeral_data_path)
        fallback.mkdir(parents=True, exist_ok=True)
        if not _is_writable_directory(fallback):
            raise RuntimeError(f"No writable data directory is available: {fallback}")
        return fallback

    @property
    def user_skills_dir(self) -> str:
        """Directory for user-uploaded skills. Must persist across HF
        Space rebuilds, so we use /data/user_skills on HF Space and
        ./data/user_skills locally. Directory is created on first access.
        """
        base = self.hf_storage_dir if self.is_hf_space else Path(self.local_user_skills_path)
        d = base / "user_skills" if self.is_hf_space else base
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
    def effective_auth_secret(self) -> str:
        """Return the signing secret; production must never use a default."""
        if self.auth_secret:
            return self.auth_secret
        if self.is_production:
            raise RuntimeError("AUTH_SECRET must be configured in production")
        return "development-only-secret-change-before-production"

    @property
    def operator_api_key(self) -> str:
        return self.admin_api_key or self.api_key

    @property
    def auth_token_ttl_seconds(self) -> int:
        return max(1, self.auth_token_ttl_hours) * 60 * 60

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
