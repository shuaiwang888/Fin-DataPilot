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

from app.agent.nodes.reflector import _infer_empty_result_recovery, reflector_node


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


def test_empty_financial_result_retries_with_original_query() -> None:
    calls = [{
        "name": "financial-query",
        "args": {"query": "宁德时代近期行情"},
        "ok": True,
        "result": {"data": {}, "ok": True},
    }]

    skill, args, reason = _infer_empty_result_recovery(
        calls=calls,
        user_query="宁德时代为什么跌",
    )

    assert skill == "financial-query"
    assert args == {"query": "宁德时代为什么跌"}
    assert "原始问句" in reason


def test_empty_financial_result_falls_back_to_anysearch_after_retry() -> None:
    calls = [
        {
            "name": "financial-query",
            "args": {"query": "宁德时代近期行情"},
            "ok": True,
            "result": {"data": {}, "ok": True},
        },
        {
            "name": "financial-query",
            "args": {"query": "宁德时代为什么跌"},
            "ok": True,
            "result": {"data": {}, "ok": True},
        },
    ]

    skill, args, reason = _infer_empty_result_recovery(
        calls=calls,
        user_query="宁德时代为什么跌",
    )

    assert skill == "anysearch"
    assert args == {
        "action": "search",
        "query": "宁德时代为什么跌",
        "domain": "finance",
        "max_results": 5,
    }
    assert "anysearch" in reason


@pytest.mark.asyncio
async def test_reflector_empty_result_emits_recovery_hint() -> None:
    state = {
        "user_query": "宁德时代为什么跌",
        "tool_calls": [
            {
                "name": "financial-query",
                "args": {"query": "宁德时代近期行情"},
                "ok": True,
                "result": {"data": {}, "ok": True},
                "error": None,
                "trace_id": "x",
            }
        ],
        "rounds_used": 0,
    }

    out = await reflector_node(state)  # type: ignore[arg-type]

    assert out["reflection_verdict"] == "need_more"
    assert out["next_skill_hint"] == "financial-query"
    assert out["next_args_hint"] == {"query": "宁德时代为什么跌"}
