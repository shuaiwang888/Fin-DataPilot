"""Tests for runtime user-skill upload: install/uninstall/startup-reload."""
from __future__ import annotations

import importlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

from app.skills import user_uploads
from app.skills.registry import REGISTRY


TEST_SKILL_NAME = "user_test_skill"

VALID_HANDLER = '''"""A trivial user skill used in tests."""
from app.skills.base import ToolParameter, ToolResult, ToolSpec
from app.skills.registry import REGISTRY


async def _handler(*, query: str) -> ToolResult:
    return ToolResult(tool="user_test_skill", ok=True, data={"echo": query})


SPEC = ToolSpec(
    name="user_test_skill",
    display_name="Test",
    description="Test skill for upload tests.",
    category="test",
    parameters=[ToolParameter(name="query", type="string", description="q")],
    requires=[],
)


REGISTRY.register(SPEC, _handler)
'''

VALID_SKILL_MD = """---
name: user_test_skill
description: A trivial user skill used in tests.
---
"""


# ----------------- fixtures -----------------

@pytest.fixture(autouse=True)
def _isolate_user_skills_dir(tmp_path, monkeypatch):
    """Point Settings.user_skills_dir at a fresh tmp dir for every test,
    and reset the relevant config caches so the new path takes effect."""
    from app import config as cfg
    importlib.reload(cfg)
    monkeypatch.setattr(cfg, "_settings", None)
    monkeypatch.setenv("LOCAL_USER_SKILLS_PATH", str(tmp_path / "user_skills"))
    s = cfg.get_settings()
    assert s.user_skills_dir.startswith(str(tmp_path)), s.user_skills_dir
    yield
    # Cleanup: drop any cached module + REGISTRY entry
    sys.modules.pop(TEST_SKILL_NAME, None)
    if REGISTRY.get_spec(TEST_SKILL_NAME) is not None:
        REGISTRY.unregister(TEST_SKILL_NAME)
    # Drop target_root from sys.path so a fresh test can re-add cleanly
    p = str(Path(s.user_skills_dir).resolve())
    while p in sys.path:
        sys.path.remove(p)


def _make_zip_bytes(entries: dict[str, str]) -> bytes:
    """Build an in-memory zip with the given {path: content} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _make_valid_zip(name: str = TEST_SKILL_NAME) -> bytes:
    return _make_zip_bytes(
        {
            f"{name}/SKILL.md": VALID_SKILL_MD.replace(
                TEST_SKILL_NAME, name
            ) if name != TEST_SKILL_NAME else VALID_SKILL_MD,
            f"{name}/{name}.py": VALID_HANDLER.replace(
                TEST_SKILL_NAME, name
            ) if name != TEST_SKILL_NAME else VALID_HANDLER,
        }
    )


# ----------------- happy path -----------------

def test_happy_path():
    """A well-formed zip installs, hot-loads, and the new skill is
    immediately registered in REGISTRY."""
    result = user_uploads.install_skill_from_zip(_make_valid_zip())
    assert result["name"] == TEST_SKILL_NAME
    assert result["uploaded"] is True
    assert result["enabled"] is True
    assert REGISTRY.get_spec(TEST_SKILL_NAME) is not None
    spec = REGISTRY.get_spec(TEST_SKILL_NAME)
    assert spec.description == "Test skill for upload tests."


# ----------------- shape validation -----------------

def test_flat_zip_with_stray_py_installs_as_prompt_only():
    """Flat zip (SKILL.md at root) with an extra .py is accepted and
    installed as a prompt-only skill — the stray .py is just ignored,
    same as any other ancillary file. Mismatched-filename rejection
    only applies in the directory layout, where a stray .py is much
    more likely to be a real handler typo."""
    blob = _make_zip_bytes(
        {"SKILL.md": VALID_SKILL_MD, "unrelated.py": "x = 1\n"}
    )
    result = user_uploads.install_skill_from_zip(blob)
    assert result["kind"] == "prompt"
    assert result["name"] == TEST_SKILL_NAME


def test_zip_with_two_top_dirs():
    """Zip with two top-level dirs, each with its own SKILL.md, is
    rejected because we can't tell which one is the skill."""
    blob = _make_zip_bytes(
        {
            "alpha/SKILL.md": "---\nname: alpha\n---\n",
            "alpha/alpha.py": VALID_HANDLER.replace(TEST_SKILL_NAME, "alpha"),
            "beta/SKILL.md": "---\nname: beta\n---\n",
            "beta/beta.py": VALID_HANDLER.replace(TEST_SKILL_NAME, "beta"),
        }
    )
    with pytest.raises(ValueError, match="Multiple SKILL.md"):
        user_uploads.install_skill_from_zip(blob)


def test_path_traversal_blocked():
    """An entry with `..` in its path is rejected before extraction."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{TEST_SKILL_NAME}/SKILL.md", VALID_SKILL_MD)
        zf.writestr(f"{TEST_SKILL_NAME}/{TEST_SKILL_NAME}.py", VALID_HANDLER)
        zf.writestr("../../etc/passwd", "pwned")
    with pytest.raises(ValueError, match="Unsafe path"):
        user_uploads.install_skill_from_zip(buf.getvalue())


def test_zip_bomb_blocked(monkeypatch):
    """A zip that decompresses to more than max_skill_upload_bytes is
    rejected during the pre-flight, not after extraction."""
    monkeypatch.setenv("MAX_SKILL_UPLOAD_BYTES", "1024")  # 1 KB
    from app import config as cfg
    importlib.reload(cfg)
    cfg._settings = None
    big = "x" * 4096
    blob = _make_zip_bytes(
        {
            f"{TEST_SKILL_NAME}/SKILL.md": VALID_SKILL_MD,
            f"{TEST_SKILL_NAME}/big.txt": big,
        }
    )
    with pytest.raises(ValueError, match="exceed"):
        user_uploads.install_skill_from_zip(blob)


# ----------------- name + conflict checks -----------------

def test_builtin_conflict():
    """Uploading a skill whose name collides with a built-in is rejected."""
    for builtin in user_uploads.BUILTIN_SKILLS:
        md = f"---\nname: {builtin}\ndescription: test\n---\n"
        py = VALID_HANDLER.replace(TEST_SKILL_NAME, builtin)
        blob = _make_zip_bytes({f"{builtin}/SKILL.md": md, f"{builtin}/{builtin}.py": py})
        with pytest.raises(ValueError, match="conflicts with a built-in"):
            user_uploads.install_skill_from_zip(blob)


def test_duplicate_upload():
    """Re-uploading an already-uploaded skill is rejected."""
    user_uploads.install_skill_from_zip(_make_valid_zip())
    with pytest.raises(ValueError, match="already"):
        user_uploads.install_skill_from_zip(_make_valid_zip())


# ----------------- handler validation -----------------

def test_syntax_error_in_handler_rolls_back():
    """A handler with a Python syntax error fails to install, and the
    extracted directory is removed (no leftover files)."""
    bad_handler = "def _handler(:\n    pass\n"  # syntax error
    blob = _make_zip_bytes(
        {
            f"{TEST_SKILL_NAME}/SKILL.md": VALID_SKILL_MD,
            f"{TEST_SKILL_NAME}/{TEST_SKILL_NAME}.py": bad_handler,
        }
    )
    with pytest.raises(ValueError, match="syntax errors"):
        user_uploads.install_skill_from_zip(blob)
    from app.config import get_settings
    skill_root = Path(get_settings().user_skills_dir)
    assert list(skill_root.iterdir()) == [], "extracted dir should be cleaned up"


def test_import_error_rolls_back():
    """A handler that imports cleanly but raises at module top-level
    is rolled back (no REGISTRY entry, no directory)."""
    bad_handler = "raise RuntimeError('intentional import-time boom')\n"
    blob = _make_zip_bytes(
        {
            f"{TEST_SKILL_NAME}/SKILL.md": VALID_SKILL_MD,
            f"{TEST_SKILL_NAME}/{TEST_SKILL_NAME}.py": bad_handler,
        }
    )
    with pytest.raises(ValueError, match="raised on import"):
        user_uploads.install_skill_from_zip(blob)
    assert REGISTRY.get_spec(TEST_SKILL_NAME) is None
    from app.config import get_settings
    skill_root = Path(get_settings().user_skills_dir)
    assert list(skill_root.iterdir()) == [], "extracted dir should be cleaned up"


def test_no_register_call_rolls_back():
    """A handler that imports cleanly but doesn't call REGISTRY.register
    is rejected and rolled back."""
    no_register = "X = 1\n"  # syntactically fine, registers nothing
    blob = _make_zip_bytes(
        {
            f"{TEST_SKILL_NAME}/SKILL.md": VALID_SKILL_MD,
            f"{TEST_SKILL_NAME}/{TEST_SKILL_NAME}.py": no_register,
        }
    )
    with pytest.raises(ValueError, match="did not call REGISTRY.register"):
        user_uploads.install_skill_from_zip(blob)
    assert REGISTRY.get_spec(TEST_SKILL_NAME) is None


def test_handler_filename_must_match_dir():
    """The handler filename must match the top-level directory name —
    if a .py exists but is misnamed, we reject the upload (rather than
    silently demoting to a prompt-only skill, which would hide a real
    typo from the user)."""
    blob = _make_zip_bytes(
        {
            f"{TEST_SKILL_NAME}/SKILL.md": VALID_SKILL_MD,
            f"{TEST_SKILL_NAME}/wrong_name.py": VALID_HANDLER,
        }
    )
    with pytest.raises(ValueError, match="must be named"):
        user_uploads.install_skill_from_zip(blob)


def test_frontmatter_name_must_match_dir():
    """SKILL.md's frontmatter `name:` must equal the directory name."""
    mismatched_md = VALID_SKILL_MD.replace(
        f"name: {TEST_SKILL_NAME}", "name: different_name"
    )
    blob = _make_zip_bytes(
        {
            f"{TEST_SKILL_NAME}/SKILL.md": mismatched_md,
            f"{TEST_SKILL_NAME}/{TEST_SKILL_NAME}.py": VALID_HANDLER,
        }
    )
    with pytest.raises(ValueError, match="does not match"):
        user_uploads.install_skill_from_zip(blob)


# ----------------- uninstall -----------------

def test_delete_builtin_refused():
    """Deleting a built-in skill is refused."""
    for builtin in user_uploads.BUILTIN_SKILLS:
        with pytest.raises(ValueError, match="Cannot delete built-in"):
            user_uploads.uninstall_skill(builtin)


def test_delete_uploaded_removes_everything():
    """Deleting an uploaded skill removes the REGISTRY entry AND the
    directory on disk."""
    user_uploads.install_skill_from_zip(_make_valid_zip())
    assert REGISTRY.get_spec(TEST_SKILL_NAME) is not None
    user_uploads.uninstall_skill(TEST_SKILL_NAME)
    assert REGISTRY.get_spec(TEST_SKILL_NAME) is None
    from app.config import get_settings
    skill_root = Path(get_settings().user_skills_dir) / TEST_SKILL_NAME
    assert not skill_root.exists(), "directory should be removed"


def test_delete_unknown_raises():
    """Deleting a skill that doesn't exist returns a clear error."""
    with pytest.raises(ValueError, match="not registered"):
        user_uploads.uninstall_skill("nonexistent_skill_xyz")


# ----------------- startup reload -----------------

def test_load_uploaded_skills_at_startup():
    """A skill previously installed in user_skills_dir is re-imported
    on startup and ends up in REGISTRY."""
    from app.config import get_settings
    skill_root = Path(get_settings().user_skills_dir) / TEST_SKILL_NAME
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(VALID_SKILL_MD, encoding="utf-8")
    (skill_root / f"{TEST_SKILL_NAME}.py").write_text(VALID_HANDLER, encoding="utf-8")

    n = user_uploads.load_uploaded_skills_at_startup()
    assert n == 1
    assert REGISTRY.get_spec(TEST_SKILL_NAME) is not None
