"""Verify config wiring (no actual network calls)."""
from app.config import get_settings


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


def test_database_url_is_sqlite() -> None:
    s = get_settings()
    assert "sqlite" in s.database_url
