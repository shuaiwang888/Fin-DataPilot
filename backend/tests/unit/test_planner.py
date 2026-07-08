"""Tests for the planner node and the plan-driven routing path."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.nodes.planner import _try_parse_plan, planner_node
from app.agent.nodes.skill_router import _substitute_placeholders


# --- _try_parse_plan: JSON extraction --------------------------------


def test_parse_plan_direct_json() -> None:
    text = json.dumps({
        "plan": [
            {"goal": "x", "target_skill": "financial-query", "args": {"query": "y"}},
        ],
        "rationale": "test",
    })
    parsed = _try_parse_plan(text)
    assert parsed is not None
    assert len(parsed["plan"]) == 1
    assert parsed["plan"][0]["target_skill"] == "financial-query"
    assert parsed["plan"][0]["args"]["query"] == "y"


def test_parse_plan_markdown_fenced() -> None:
    text = "```json\n" + json.dumps({
        "plan": [{"goal": "x", "target_skill": "anysearch", "args": {}}],
    }) + "\n```"
    parsed = _try_parse_plan(text)
    assert parsed is not None and len(parsed["plan"]) == 1


def test_parse_plan_invalid_returns_none() -> None:
    assert _try_parse_plan("not json at all") is None
    assert _try_parse_plan(json.dumps({"foo": "bar"})) is None  # no plan key


def test_parse_plan_filters_invalid_step_shapes() -> None:
    text = json.dumps({
        "plan": [
            {"goal": "good", "target_skill": "financial-query", "args": {"q": "x"}},
            "not a dict",  # skipped
            {"goal": "ok"},  # missing target_skill + args → target_skill=None
        ],
    })
    parsed = _try_parse_plan(text)
    assert parsed is not None
    # Non-dict entries are skipped, but the "ok" dict with missing
    # fields is kept (target_skill=None, args={} are valid defaults).
    assert len(parsed["plan"]) == 2
    assert parsed["plan"][0]["target_skill"] == "financial-query"
    assert parsed["plan"][1]["target_skill"] is None


# --- planner_node: end-to-end with stubbed LLM ----------------------


@pytest.mark.asyncio
async def test_planner_produces_plan_from_llm() -> None:
    state = {
        "user_query": "茅台股价多少",
        "history": [],
        "tool_calls": [],
        "plan": [],
        "pending_step_index": 0,
    }
    fake_plan = {
        "plan": [
            {"goal": "取最新价", "target_skill": "financial-query",
             "args": {"query": "贵州茅台 最新价"}},
        ],
        "rationale": "单步",
    }
    with patch("app.agent.nodes.planner.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": json.dumps(fake_plan)})())
        mock_build.return_value = llm
        out = await planner_node(state)  # type: ignore[arg-type]

    assert len(out["plan"]) == 1
    assert out["plan"][0]["target_skill"] == "financial-query"
    assert out["pending_step_index"] == 0
    assert llm.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_planner_drops_unknown_or_disabled_skill() -> None:
    from app.skills.registry import REGISTRY
    state = {
        "user_query": "x",
        "history": [], "tool_calls": [], "plan": [], "pending_step_index": 0,
    }
    REGISTRY.set_enabled("report-search", False)
    try:
        fake_plan = {
            "plan": [
                {"goal": "ok", "target_skill": "financial-query", "args": {}},
                {"goal": "bad1", "target_skill": "totally_made_up", "args": {}},
                {"goal": "bad2", "target_skill": "report-search", "args": {}},  # disabled
            ],
        }
        with patch("app.agent.nodes.planner.build_chat_model") as mock_build:
            llm = AsyncMock()
            llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": json.dumps(fake_plan)})())
            mock_build.return_value = llm
            out = await planner_node(state)  # type: ignore[arg-type]
        # Only the financial-query step survives.
        assert len(out["plan"]) == 1
        assert out["plan"][0]["target_skill"] == "financial-query"
    finally:
        REGISTRY.set_enabled("report-search", True)


@pytest.mark.asyncio
async def test_planner_falls_back_when_all_steps_invalid() -> None:
    state = {
        "user_query": "x",
        "history": [], "tool_calls": [], "plan": [], "pending_step_index": 0,
    }
    fake_plan = {"plan": [{"goal": "x", "target_skill": "totally_made_up", "args": {}}]}
    with patch("app.agent.nodes.planner.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": json.dumps(fake_plan)})())
        mock_build.return_value = llm
        out = await planner_node(state)  # type: ignore[arg-type]
    # Fallback: empty plan → router's LLM path drives the question
    # reactively. (Previously this returned a 1-step null-skill plan
    # that short-circuited to "（按计划在第 1 步直接输出答案。）".)
    assert out["plan"] == []
    assert out["pending_step_index"] == 0


@pytest.mark.asyncio
async def test_planner_falls_back_when_parse_fails() -> None:
    state = {
        "user_query": "x",
        "history": [], "tool_calls": [], "plan": [], "pending_step_index": 0,
    }
    with patch("app.agent.nodes.planner.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": "totally not JSON"})())
        mock_build.return_value = llm
        out = await planner_node(state)  # type: ignore[arg-type]
    assert out["plan"] == []


# --- _substitute_placeholders ----------------------------------------


def _row(name: str, code: str, market_cap: float) -> dict:
    return {"股票代码": code, "股票简称": name, "总市值": market_cap}


def test_substitute_top_stock_replaces_with_name_and_code() -> None:
    args = {"query": "<step_0_top_stock> 最近公告", "days": "30"}
    prior = [{
        "name": "financial-query", "args": {}, "ok": True,
        "result": {"data": {"datas": [
            _row("华勤技术", "603296.SH", 1169.57),
            _row("茅台", "600519.SH", 2000.0),
        ]}},
    }]
    out = _substitute_placeholders(args, prior)
    # Top market cap is 茅台 (2000.0)
    assert "茅台" in out["query"]
    assert "600519" in out["query"]


def test_substitute_top_name_only() -> None:
    args = {"query": "<step_0_top_name> 公告"}
    prior = [{"result": {"data": {"datas": [_row("华勤技术", "603296.SH", 1000.0)]}}}]
    out = _substitute_placeholders(args, prior)
    assert out["query"] == "华勤技术 公告"


def test_substitute_top_code_only() -> None:
    args = {"query": "<step_0_top_code> 公告"}
    prior = [{"result": {"data": {"datas": [_row("华勤技术", "603296.SH", 1000.0)]}}}]
    out = _substitute_placeholders(args, prior)
    assert out["query"] == "603296.SH 公告"


def test_substitute_no_prior_returns_args_unchanged() -> None:
    args = {"query": "<step_0_top_stock> 公告"}
    out = _substitute_placeholders(args, [])
    # Pattern stays literal (no prior to substitute with).
    assert "<step_0_top_stock>" in out["query"]


def test_substitute_walks_nested_dicts() -> None:
    args = {"filter": {"name": "<step_0_top_name>", "code": "<step_0_top_code>"}}
    prior = [{"result": {"data": {"datas": [_row("华勤技术", "603296.SH", 1000.0)]}}}]
    out = _substitute_placeholders(args, prior)
    assert out["filter"]["name"] == "华勤技术"
    assert out["filter"]["code"] == "603296.SH"
