"""Tests for prompt-only (SKILL.md-only) skills — the second kind of
uploaded skill alongside code skills."""
from __future__ import annotations

import importlib
import sys
import zipfile
from pathlib import Path

import pytest

from app.agent.nodes.reflector import reflector_node
from app.skills import user_uploads
from app.skills.registry import REGISTRY


PROMPT_SKILL_NAME = "user_glossary_skill"

PROMPT_SKILL_MD_FULL = """---
name: user_glossary_skill
displayName: 金融术语速查
description: 金融领域常见术语解释，用于 LLM 在用户提问时给出准确含义。
version: 0.1.0
---

# 金融术语速查

当用户问到以下概念时，使用下面的定义：

- **多头**：预期价格上涨而买入持有的头寸。
- **空头**：预期价格下跌而卖出的头寸。
- **平仓**：了结已开仓的头寸。
- **市盈率（P/E）**：股价 / 每股收益。
- **市净率（P/B）**：股价 / 每股净资产。

如用户问到的术语未在表中，请按通用理解回答并注明。
"""

PROMPT_SKILL_MD_MINIMAL = """---
name: user_glossary_skill
description: 极简 prompt skill。
---
"""


# ----------------- fixtures -----------------

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Per-test fresh user_skills_dir + registry cleanup."""
    from app import config as cfg
    importlib.reload(cfg)
    monkeypatch.setattr(cfg, "_settings", None)
    monkeypatch.setenv("LOCAL_USER_SKILLS_PATH", str(tmp_path / "user_skills"))
    s = cfg.get_settings()
    yield
    sys.modules.pop(PROMPT_SKILL_NAME, None)
    if REGISTRY.get_spec(PROMPT_SKILL_NAME) is not None:
        REGISTRY.unregister(PROMPT_SKILL_NAME)
    REGISTRY.set_prompt_body(PROMPT_SKILL_NAME, None)
    p = str(Path(s.user_skills_dir).resolve())
    while p in sys.path:
        sys.path.remove(p)


def _make_zip(entries: dict[str, str]) -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _make_flat_prompt_zip(body: str = PROMPT_SKILL_MD_FULL) -> bytes:
    """Zip with just SKILL.md at the root (no top-level dir)."""
    return _make_zip({"SKILL.md": body})


def _make_dir_prompt_zip(body: str = PROMPT_SKILL_MD_FULL) -> bytes:
    """Zip with a single top-level dir containing SKILL.md (no .py)."""
    return _make_zip({f"{PROMPT_SKILL_NAME}/SKILL.md": body})


# ----------------- happy paths -----------------

def test_flat_zip_installs_as_prompt():
    """A flat zip with just SKILL.md installs as a prompt-only skill."""
    result = user_uploads.install_skill_from_zip(_make_flat_prompt_zip())
    assert result["name"] == PROMPT_SKILL_NAME
    assert result["kind"] == "prompt"
    assert result["uploaded"] is True
    assert result["enabled"] is True
    assert REGISTRY.get_spec(PROMPT_SKILL_NAME) is not None
    spec = REGISTRY.get_spec(PROMPT_SKILL_NAME)
    assert spec.parameters == []
    assert spec.display_name == "金融术语速查"  # from frontmatter
    assert "金融领域常见术语" in spec.description


def test_dir_layout_zip_installs_as_prompt():
    """The directory layout (no .py) also installs as prompt-only."""
    result = user_uploads.install_skill_from_zip(_make_dir_prompt_zip())
    assert result["kind"] == "prompt"
    assert REGISTRY.get_spec(PROMPT_SKILL_NAME) is not None


def test_prompt_body_lands_in_to_prompt_text():
    """The SKILL.md body is injected into REGISTRY.to_prompt_text so
    the LLM sees the domain knowledge in its system prompt."""
    user_uploads.install_skill_from_zip(_make_flat_prompt_zip())
    text = REGISTRY.to_prompt_text()
    assert "user_glossary_skill" in text
    # The body should be there (not just the description).
    assert "市盈率（P/E）" in text
    # The spec's `description` should also still be there.
    assert "金融领域常见术语" in text


def test_prompt_dispatch_returns_body():
    """REGISTRY.dispatch returns the body wrapped in ToolResult so the
    frontend can show / debug it."""
    import asyncio
    user_uploads.install_skill_from_zip(_make_flat_prompt_zip())
    result = asyncio.run(REGISTRY.dispatch(PROMPT_SKILL_NAME, {}))
    assert result.ok
    assert "skill_body" in result.data
    assert "市盈率（P/E）" in result.data["skill_body"]
    assert result.meta.get("prompt_only") is True


def test_prompt_body_truncated_when_too_long():
    """A very large SKILL.md body gets truncated in to_prompt_text so
    the system prompt doesn't blow up."""
    from app.skills.registry import MAX_PROMPT_BODY_CHARS
    huge_body = "X" * (MAX_PROMPT_BODY_CHARS * 3) + "\n# " + PROMPT_SKILL_MD_FULL
    blob = _make_flat_zip_with_body(huge_body)
    user_uploads.install_skill_from_zip(blob)
    text = REGISTRY.to_prompt_text()
    # Truncation marker present
    assert "已截断" in text
    # And the in-memory registry still has the FULL body for dispatch
    assert len(REGISTRY.get_prompt_body(PROMPT_SKILL_NAME)) > MAX_PROMPT_BODY_CHARS


def _make_flat_zip_with_body(body: str) -> bytes:
    md = f"---\nname: {PROMPT_SKILL_NAME}\ndescription: huge\n---\n\n{body}"
    return _make_flat_prompt_zip(md)


# ----------------- validation failures -----------------

def test_no_skill_md_at_all():
    """Zip without any SKILL.md is rejected."""
    blob = _make_zip({"README.md": "no skill here"})
    with pytest.raises(ValueError, match="No SKILL.md"):
        user_uploads.install_skill_from_zip(blob)


def test_multiple_skill_md():
    """Zip with two SKILL.md files is rejected."""
    blob = _make_zip({
        f"{PROMPT_SKILL_NAME}/SKILL.md": PROMPT_SKILL_MD_FULL,
        "extra/SKILL.md": "---\nname: extra\n---\n",
    })
    with pytest.raises(ValueError, match="Multiple SKILL.md"):
        user_uploads.install_skill_from_zip(blob)


def test_no_frontmatter_name():
    """SKILL.md with no frontmatter `name:` is rejected (flat layout)."""
    blob = _make_zip({"SKILL.md": "no frontmatter at all, just text"})
    with pytest.raises(ValueError, match="frontmatter to include"):
        user_uploads.install_skill_from_zip(blob)


def test_frontmatter_name_mismatch_in_dir_layout():
    """Dir-layout zip where frontmatter name != directory name is rejected."""
    blob = _make_zip({
        "other_name/SKILL.md": PROMPT_SKILL_MD_FULL,
    })
    with pytest.raises(ValueError, match="does not match"):
        user_uploads.install_skill_from_zip(blob)


def test_builtin_name_prompt():
    """Trying to upload a prompt skill with a built-in name is rejected."""
    blob = _make_zip({
        "financial-query/SKILL.md": "---\nname: financial-query\ndescription: x\n---\n",
    })
    with pytest.raises(ValueError, match="conflicts with a built-in"):
        user_uploads.install_skill_from_zip(blob)


# ----------------- uninstall + restart-survival -----------------

def test_uninstall_prompt_skill():
    """Uninstalling a prompt skill removes the REGISTRY entry and
    the on-disk directory."""
    user_uploads.install_skill_from_zip(_make_flat_prompt_zip())
    assert REGISTRY.get_spec(PROMPT_SKILL_NAME) is not None
    user_uploads.uninstall_skill(PROMPT_SKILL_NAME)
    assert REGISTRY.get_spec(PROMPT_SKILL_NAME) is None
    assert REGISTRY.get_prompt_body(PROMPT_SKILL_NAME) is None
    from app.config import get_settings
    skill_dir = Path(get_settings().user_skills_dir) / PROMPT_SKILL_NAME
    assert not skill_dir.exists()


def test_startup_reload_prompt_skill():
    """A prompt-only skill that was previously installed (just SKILL.md
    on disk) gets re-registered on startup."""
    from app.config import get_settings
    skill_dir = Path(get_settings().user_skills_dir) / PROMPT_SKILL_NAME
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(PROMPT_SKILL_MD_FULL, encoding="utf-8")

    n = user_uploads.load_uploaded_skills_at_startup()
    assert n == 1
    spec = REGISTRY.get_spec(PROMPT_SKILL_NAME)
    assert spec is not None
    assert spec.display_name == "金融术语速查"
    # Body must be in the registry's prompt cache for the LLM to see it
    assert "市盈率（P/E）" in (REGISTRY.get_prompt_body(PROMPT_SKILL_NAME) or "")


# ----------------- reflector integration -----------------

@pytest.mark.asyncio
async def test_reflector_treats_prompt_body_as_sufficient():
    """When a prompt-only skill returns data.skill_body, the reflector
    must NOT fire the empty-data heuristic and loop forever asking for
    more data."""
    last_call = {
        "name": PROMPT_SKILL_NAME,
        "args": {},
        "result": {
            "tool": PROMPT_SKILL_NAME,
            "ok": True,
            "data": {"skill_body": "lots of domain knowledge"},
            "error": None,
            "trace_id": "abc",
            "duration_ms": 0,
            "meta": {"prompt_only": True},
        },
        "ok": True,
        "duration_ms": 0,
        "error": None,
        "trace_id": "abc",
    }
    state = {
        "user_query": "什么是多头？",
        "tool_calls": [last_call],
        "rounds_used": 1,
    }
    out = await reflector_node(state)
    assert out["reflection_verdict"] == "sufficient"
    assert "prompt-only" in out["reflection"]


@pytest.mark.asyncio
async def test_reflector_still_need_more_for_empty_code_skill():
    """Regression: code skills returning empty data should still get
    'need_more' from the reflector (this behavior must not be broken
    by the prompt-only change)."""
    state = {
        "user_query": "find me some stocks",
        "tool_calls": [
            {
                "name": "financial-query",
                "args": {},
                "result": {"tool": "financial-query", "ok": True, "data": {}, "error": None},
                "ok": True,
                "duration_ms": 0,
                "error": None,
                "trace_id": "x",
            }
        ],
        "rounds_used": 1,
    }
    out = await reflector_node(state)
    assert out["reflection_verdict"] == "need_more"
