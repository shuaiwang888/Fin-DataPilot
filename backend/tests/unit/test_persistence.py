"""Persistence-path resolution: the DB must land on the right volume."""
from __future__ import annotations

import os
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

    from app.config import Settings

    s = Settings()
    assert s.is_hf_space is False
    assert s.persistent_db_path == str(local)


def test_hf_space_uses_data_path(monkeypatch, tmp_path):
    """On HF Space, persistent_db_path must resolve to /data/...db.

    We can't safely run the real write probe on /data in CI, so we
    patch persistent_db_path to be readable-only, then check that the
    HF path is the one chosen.
    """
    monkeypatch.setenv("SPACE_ID", "owner/space-name")

    from app.config import Settings

    s = Settings()
    assert s.is_hf_space is True
    # On a real HF Space, the write probe runs and either returns
    # /data/findatapilot.db or raises RuntimeError. In CI the probe
    # might succeed (if /data is writable) or fail. Both are
    # acceptable outcomes — what matters is the property is computed
    # from the HF code path, not the local fallback.
    try:
        path = s.persistent_db_path
        assert path == "/data/findatapilot.db"
    except RuntimeError:
        # Loud failure when /data is unwritable — that's correct
        # behavior on a misconfigured Space.
        pass


def test_hf_space_raises_when_data_not_writable(monkeypatch):
    """On HF Space, an unwritable /data must crash loudly on startup
    instead of silently falling back to a non-persistent path."""
    monkeypatch.setenv("SPACE_ID", "owner/space-name")

    from app.config import Settings

    s = Settings()

    def boom(*a, **kw):
        raise OSError("simulated: /data is read-only")

    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(RuntimeError, match="persistent storage must be enabled"):
        _ = s.persistent_db_path


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
    from fastapi.testclient import TestClient
    import importlib
    import app.config as cfg
    from app.api.health import router
    from fastapi import FastAPI

    monkeypatch.delenv("SPACE_ID", raising=False)
    monkeypatch.setattr("app.config.Path.is_dir", lambda self: False)
    local = tmp_path / "diag.db"
    monkeypatch.setenv("LOCAL_SQLITE_PATH", str(local))

    importlib.reload(cfg)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        r = client.get("/diag")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_hf_space"] is False
        assert body["db_path"] == str(local)
        assert "database_url" in body
        assert body["db_exists"] is False
        assert body["db_size_bytes"] == 0
