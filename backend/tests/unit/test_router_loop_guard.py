"""Tests for the router's loop-detection guards.

The router has three guards that catch different "stuck on one thing"
patterns:
  1. Identical-args loop: last 2 calls byte-identical
  2. Same-skill loop: last 3 calls all the same skill (catches the
     "LLM keeps re-planning with slightly different args" case)
  3. Zero-result retry: same skill + 2/3 of last calls returned 0
     rows
All three bail with an honest final_answer + a "failed" verdict.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.nodes.skill_router import skill_router_node


def _call(name: str, args: dict[str, Any], rows: list | None = None, ok: bool = True) -> dict[str, Any]:
    """Build a tool_call record. rows=None means "non-dict data,
    treat as 0-row" (e.g. anysearch Markdown). rows=[] also 0-row.
    rows=[{...}] means non-empty data."""
    if rows is None:
        data = {}  # empty dict → 0 rows
    else:
        data = {"datas": rows}
    return {
        "name": name,
        "args": args,
        "ok": ok,
        "result": {"data": data, "ok": ok},
        "trace_id": "t",
        "duration_ms": 0,
        "error": None,
    }


@pytest.mark.asyncio
async def test_router_bails_on_identical_args_loop() -> None:
    """Two consecutive identical (name, args) tool_calls → bail."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("announcement-search", {"query": "admin", "limit": "10", "days": "30"}),
            _call("announcement-search", {"query": "admin", "limit": "10", "days": "30"}),
        ],
        "rounds_used": 0,
        "history": [],
        "plan": [],
        "pending_step_index": 0,
        "next_skill_hint": None,
        "next_args_hint": None,
    }
    # No LLM mock needed — the loop guard should fire BEFORE any
    # LLM call. Patch build_chat_model anyway in case the guard
    # doesn't fire and the test leaks through to the LLM path.
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock()
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]

    assert out.get("reflection_verdict") == "failed"
    assert "announcement-search" in out.get("final_answer", "")
    assert "未拿到有效数据" in out.get("final_answer", "")
    # Crucially, the LLM was NOT called.
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_router_does_not_bail_on_different_args() -> None:
    """Two tool_calls with different args → no bail."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("announcement-search", {"query": "admin", "days": "30"}),
            _call("announcement-search", {"query": "茅台", "days": "30"}),  # different
        ],
        "rounds_used": 0,
        "history": [],
        "plan": [],
        "pending_step_index": 0,
        "next_skill_hint": None,
        "next_args_hint": None,
    }
    # Stub the LLM so we don't try to actually invoke it.
    fake_response = json.dumps({"name": "news-search", "args": {"query": "y"}})
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_response})())
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]
    # No bail — the LLM path ran.
    assert "final_answer" not in out or "未拿到有效数据" not in out.get("final_answer", "")
    llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_router_does_not_bail_on_different_skills() -> None:
    """Same args, different skills → not a loop."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("financial-query", {"query": "x"}),
            _call("news-search", {"query": "x"}),  # different skill
        ],
        "rounds_used": 0,
        "history": [],
        "plan": [],
        "pending_step_index": 0,
        "next_skill_hint": None,
        "next_args_hint": None,
    }
    fake_response = json.dumps({"name": "anysearch", "args": {"action": "search", "query": "x"}})
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_response})())
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]
    assert "未拿到有效数据" not in out.get("final_answer", "")
    llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_router_does_not_bail_with_one_history_call() -> None:
    """Only 1 prior tool_call → no loop possible."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("financial-query", {"query": "x"}),
        ],
        "rounds_used": 0,
        "history": [],
        "plan": [],
        "pending_step_index": 0,
        "next_skill_hint": None,
        "next_args_hint": None,
    }
    fake_response = json.dumps({"name": "news-search", "args": {"query": "y"}})
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_response})())
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]
    assert "未拿到有效数据" not in out.get("final_answer", "")


# ---- Same-skill + zero-result guards (the LLM-hallucinated-args case)


@pytest.mark.asyncio
async def test_router_bails_on_same_skill_with_slightly_different_args() -> None:
    """The original bug: LLM re-plans with 'admin', 'admin1',
    'admin2'. Strict equals misses it; same-skill-with-zero-result
    catches it. The LLM is not called."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("announcement-search", {"query": "admin", "days": "30"}, rows=[]),
            _call("announcement-search", {"query": "admin1", "days": "30"}, rows=[]),
            _call("announcement-search", {"query": "admin2", "days": "30"}, rows=[]),
        ],
        "rounds_used": 0, "history": [], "plan": [], "pending_step_index": 0,
        "next_skill_hint": None, "next_args_hint": None,
    }
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock()
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]

    assert out.get("reflection_verdict") == "failed"
    assert "announcement-search" in out.get("final_answer", "")
    assert "未拿到有效数据" in out.get("final_answer", "")
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_router_does_not_bail_when_repeating_skill_with_results() -> None:
    """If the repeated calls are returning rows, the loop guard
    should NOT fire — there's no point in bailing if we're
    actually making progress."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("news-search", {"query": "a"}, rows=[{"title": "x"}]),
            _call("news-search", {"query": "b"}, rows=[{"title": "y"}]),
            _call("news-search", {"query": "c"}, rows=[{"title": "z"}]),
        ],
        "rounds_used": 0, "history": [], "plan": [], "pending_step_index": 0,
        "next_skill_hint": None, "next_args_hint": None,
    }
    fake_response = json.dumps({"name": "anysearch", "args": {"action": "search", "query": "x"}})
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_response})())
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]
    # LLM path ran (no bail)
    assert out.get("reflection_verdict") != "failed"
    llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_router_bails_with_one_zero_one_nonzero() -> None:
    """Of the last 3 same-skill calls, 2 returned 0 rows → bail."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("announcement-search", {"query": "a"}, rows=[{"title": "ok"}]),  # 1 result
            _call("announcement-search", {"query": "b"}, rows=[]),  # 0
            _call("announcement-search", {"query": "c"}, rows=[]),  # 0
        ],
        "rounds_used": 0, "history": [], "plan": [], "pending_step_index": 0,
        "next_skill_hint": None, "next_args_hint": None,
    }
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock()
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]
    assert out.get("reflection_verdict") == "failed"
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_router_does_not_bail_with_one_zero() -> None:
    """Only 1 of last 3 returned 0 → no bail (too soon to give up)."""
    state: dict[str, Any] = {
        "user_query": "x",
        "tool_calls": [
            _call("announcement-search", {"query": "a"}, rows=[{"title": "ok1"}]),
            _call("announcement-search", {"query": "b"}, rows=[{"title": "ok2"}]),
            _call("announcement-search", {"query": "c"}, rows=[]),
        ],
        "rounds_used": 0, "history": [], "plan": [], "pending_step_index": 0,
        "next_skill_hint": None, "next_args_hint": None,
    }
    fake_response = json.dumps({"name": "anysearch", "args": {"action": "search", "query": "x"}})
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_response})())
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]
    # Only 1 zero → not yet looping → LLM path
    llm.ainvoke.assert_called_once()
