"""Security contract for runtime skill uploads."""
from __future__ import annotations

import importlib
import io
import zipfile

import pytest

from app.skills import user_uploads


def _zip(entries: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return out.getvalue()


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from app import config

    importlib.reload(config)
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv("LOCAL_USER_SKILLS_PATH", str(tmp_path / "skills"))
    yield
    importlib.reload(config)


def test_upload_is_disabled_by_default() -> None:
    with pytest.raises(ValueError, match="disabled"):
        user_uploads.install_skill_from_zip(_zip({"SKILL.md": "---\nname: demo\n---\n"}))


def test_code_skill_is_rejected_even_when_operator_enables_upload(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_SKILL_UPLOAD", "true")
    with pytest.raises(ValueError, match="Code skills are not supported"):
        user_uploads.install_skill_from_zip(
            _zip(
                {
                    "demo/SKILL.md": "---\nname: demo\n---\n",
                    "demo/demo.py": "raise RuntimeError('must not execute')\n",
                }
            )
        )


def test_zip_path_traversal_is_rejected_before_install(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_SKILL_UPLOAD", "true")
    with pytest.raises(ValueError, match="Unsafe path"):
        user_uploads.install_skill_from_zip(
            _zip({"SKILL.md": "---\nname: demo\n---\n", "../../escape": "no"})
        )
