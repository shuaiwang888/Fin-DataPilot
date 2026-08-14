"""Persistence-path resolution: the DB must land on the right volume."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings_cache(monkeypatch):
    """Force a fresh Settings read for each test in this module."""
    import importlib

    import app.config as cfg

    importlib.reload(cfg)
    yield
    importlib.reload(cfg)


def test_local_dev_uses_local_path(monkeypatch, tmp_path):
    """When /data doesn't exist (normal dev), use the configured local path."""
    monkeypatch.delenv("SPACE_ID", raising=False)
    # Pretend /data doesn't exist by patching Path.is_dir
    monkeypatch.setattr("app.config.Path.is_dir", lambda self: False)
    local = tmp_path / "dev.db"
    monkeypatch.setenv("LOCAL_SQLITE_PATH", str(local))
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")

    from app.config import Settings

    s = Settings()
    assert s.is_hf_space is False
    assert s.persistent_db_path == str(local)


def test_hf_space_uses_data_path_when_writable(monkeypatch):
    """A writable HF persistent mount is always preferred."""
    monkeypatch.setenv("SPACE_ID", "owner/space-name")
    monkeypatch.setattr("app.config._is_writable_directory", lambda _path: True)

    from app.config import Settings

    s = Settings()
    assert s.is_hf_space is True
    assert s.persistent_db_path == "/data/findatapilot.db"


def test_hf_space_falls_back_when_data_not_writable(monkeypatch, tmp_path):
    """A root-owned /data mount must not prevent the API from starting."""
    monkeypatch.setenv("SPACE_ID", "owner/space-name")
    monkeypatch.setenv("HF_EPHEMERAL_DATA_PATH", str(tmp_path / "ephemeral"))
    monkeypatch.setattr(
        "app.config._is_writable_directory",
        lambda path: path != Path("/data"),
    )

    from app.config import Settings

    s = Settings()

    assert s.persistent_db_path == str(tmp_path / "ephemeral" / "findatapilot.db")


def test_turso_path_bypasses_local_resolution(monkeypatch):
    """When Turso is configured, the local /data probe should not run
    (no need to even read persistent_db_path on the Turso branch)."""
    monkeypatch.setenv("SPACE_ID", "owner/space-name")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")

    from app.config import Settings

    s = Settings()
    assert s.turso_database_url == "libsql://example.turso.io"
    assert "turso.io" in s.database_url


def test_diag_endpoint_reports_path(monkeypatch, tmp_path):
    """Sanity check: /api/diag must return the resolved DB path."""
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.config as cfg
    from app.api.health import router

    monkeypatch.delenv("SPACE_ID", raising=False)
    monkeypatch.setattr("app.config.Path.is_dir", lambda self: False)
    local = tmp_path / "diag.db"
    monkeypatch.setenv("LOCAL_SQLITE_PATH", str(local))
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")

    importlib.reload(cfg)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        r = client.get("/diag", headers={"X-API-Key": "test-admin-key"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_hf_space"] is False
        assert body["db_path"] == str(local)
        assert "database_url" in body
        assert body["db_exists"] is False
        assert body["db_size_bytes"] == 0
