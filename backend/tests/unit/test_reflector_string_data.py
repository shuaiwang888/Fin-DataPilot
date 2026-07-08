"""Regression tests for the reflector's tolerance of string-typed tool data.

The anysearch-skill returns free-form Markdown text from the CLI
(search/extract actions). The reflector used to do `data.get("datas")`
directly, which crashed with `'str' object has no attribute 'get'`.

These tests assert the fixed behaviour:
  - non-empty string → sufficient (skip LLM round-trip)
  - empty string → need_more (no content to answer with)
"""
from __future__ import annotations

import pytest

from app.agent.nodes.reflector import reflector_node


def _state_with_tool_call(name: str, *, data, ok: bool = True, error: str | None = None) -> dict:
    """Build a minimal AgentState shape the reflector reads."""
    return {
        "user_query": "测试问句",
        "tool_calls": [
            {
                "name": name,
                "args": {"action": "search", "query": "x"},
                "ok": ok,
                "result": {"data": data, "ok": ok, "error": error, "meta": {}, "tool": name},
                "error": error,
                "trace_id": "t_test",
            }
        ],
    }


@pytest.mark.asyncio
async def test_reflector_string_with_content_is_sufficient() -> None:
    """anysearch search/extract returns Markdown text — should short-circuit
    to 'sufficient' so we don't burn an LLM call trying to introspect it."""
    state = _state_with_tool_call(
        "anysearch",
        data="## Search Results (2 results)\n\n### 1. Paris\n- URL: https://...",
    )
    out = await reflector_node(state)  # type: ignore[arg-type]
    assert out["reflection_verdict"] == "sufficient"
    assert "chars" in out["reflection"]


@pytest.mark.asyncio
async def test_reflector_empty_string_is_need_more() -> None:
    """Empty string from a skill = "no results", same as empty list."""
    state = _state_with_tool_call("anysearch", data="")
    out = await reflector_node(state)  # type: ignore[arg-type]
    assert out["reflection_verdict"] == "need_more"


@pytest.mark.asyncio
async def test_reflector_whitespace_only_string_is_need_more() -> None:
    """Whitespace-only counts as empty."""
    state = _state_with_tool_call("anysearch", data="   \n  \n")
    out = await reflector_node(state)  # type: ignore[arg-type]
    assert out["reflection_verdict"] == "need_more"


@pytest.mark.asyncio
async def test_reflector_prompt_only_skill_still_works() -> None:
    """The previous skill_body short-circuit must still fire."""
    state = _state_with_tool_call("my_glossary", data={"skill_body": "## Term: P/E\n..."})
    out = await reflector_node(state)  # type: ignore[arg-type]
    assert out["reflection_verdict"] == "sufficient"
    assert "prompt-only" in out["reflection"]


@pytest.mark.asyncio
async def test_reflector_dict_with_articles_still_works() -> None:
    """Existing news-search path (dict with 'articles' key) still works."""
    state = _state_with_tool_call(
        "news-search", data={"articles": [{"title": "A"}, {"title": "B"}], "count": 2}
    )
    out = await reflector_node(state)  # type: ignore[arg-type]
    # The LLM path is taken; we just assert it didn't crash and returned
    # a valid verdict.
    assert out["reflection_verdict"] in ("sufficient", "need_more", "failed")


@pytest.mark.asyncio
async def test_reflector_failed_tool_call_is_failed() -> None:
    """ok=False should always short-circuit to 'failed' regardless of data."""
    state = _state_with_tool_call("anysearch", data=None, ok=False, error="network error")
    out = await reflector_node(state)  # type: ignore[arg-type]
    assert out["reflection_verdict"] == "failed"
    assert "network error" in out["reflection"]
