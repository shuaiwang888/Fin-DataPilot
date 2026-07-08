"""Tests for the router's loop-detection guard.

The guard fires when the last two tool_calls in state have IDENTICAL
(name, args) — the canonical "LLM keeps retrying the same failing
query" loop. We bail out with an honest final_answer rather than
burning the recursion budget.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.nodes.skill_router import skill_router_node


def _call(name: str, args: dict[str, Any], ok: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "args": args,
        "ok": ok,
        "result": {"data": {}, "ok": ok},
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
    assert "重复调用了相同的查询" in out.get("final_answer", "")
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
    assert "final_answer" not in out or "重复调用" not in out.get("final_answer", "")
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
    assert "重复调用" not in out.get("final_answer", "")
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
    assert "重复调用" not in out.get("final_answer", "")
