"""Verify config wiring (no actual network calls)."""
import pytest

from app.config import Settings, get_settings


def test_settings_load() -> None:
    s = get_settings()
    assert s.llm_model
    assert s.data_pilot_port == 7860
    assert "minimax" in s.llm_provider or s.llm_provider in (
        "openai",
        "anthropic",
        "custom",
    )


def test_cors_origins_parsed() -> None:
    s = get_settings()
    assert isinstance(s.cors_origins_list, list)
    assert len(s.cors_origins_list) >= 1


def test_agent_skill_call_budget_cannot_exceed_eight(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MAX_SKILL_CALLS", "48")

    settings = Settings()

    assert settings.agent_max_skill_calls == 8


def test_database_url_is_sqlite() -> None:
    s = get_settings()
    assert "sqlite" in s.database_url


def test_production_requires_auth_secret() -> None:
    settings = Settings(data_pilot_env="production", auth_secret="")
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        _ = settings.effective_auth_secret


def test_admin_api_key_and_legacy_alias() -> None:
    assert Settings(admin_api_key="operator").operator_api_key == "operator"
    assert Settings(api_key="legacy").operator_api_key == "legacy"
