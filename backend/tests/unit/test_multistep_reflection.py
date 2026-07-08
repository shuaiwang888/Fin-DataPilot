"""Tests for the multi-step reflection loop:
reflector emits next_skill_hint/args → router consumes them in the
next turn → executor dispatches → reflector re-evaluates.

We stub out the LLM calls so the test is deterministic + offline.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.nodes.reflector import reflector_node
from app.agent.nodes.skill_router import skill_router_node


def _state_with_calls(calls: list[dict[str, Any]], **extra) -> dict:
    return {
        "user_query": "涨停的股票中市值最大的那只最近的公告或研报",
        "tool_calls": calls,
        "rounds_used": 0,
        "history": [],
        "pending_step_index": 0,
        **extra,
    }


def _stock_row(symbol: str, name: str, market_cap: float) -> dict:
    return {"股票代码": symbol, "股票简称": name, "总市值": market_cap}


# --- reflector: multi-part question → need_more + hint ----------------


@pytest.mark.asyncio
async def test_reflector_recognises_multi_part_and_emits_hint() -> None:
    """The reflector's LLM should return need_more + next_skill_hint
    when the question has multiple sub-goals and only one is covered."""
    state = _state_with_calls([
        {
            "name": "financial-query",
            "args": {"query": "今日涨停股票中市值最大的,按总市值排名"},
            "ok": True,
            "result": {
                "data": {"datas": [
                    _stock_row("600519", "贵州茅台", 2_000_000_000_000),
                    _stock_row("601318", "中国平安", 1_500_000_000_000),
                ], "code_count": 56},
                "ok": True,
            },
            "error": None,
            "trace_id": "t1",
        }
    ])
    fake_llm_response = json.dumps({
        "verdict": "need_more",
        "reason": "已识别 top-1 股票（贵州茅台 600519），还需要它的最近公告/研报",
        "next_skill_hint": "announcement-search",
        "next_args_hint": {"query": "贵州茅台 600519 最近公告", "days": "30", "limit": "10"},
    })
    with patch("app.agent.nodes.reflector.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_llm_response})())
        mock_build.return_value = llm
        out = await reflector_node(state)  # type: ignore[arg-type]

    assert out["reflection_verdict"] == "need_more"
    assert "贵州茅台" in out["reflection"]
    assert out["next_skill_hint"] == "announcement-search"
    assert out["next_args_hint"]["query"] == "贵州茅台 600519 最近公告"
    assert out["next_args_hint"]["days"] == "30"


@pytest.mark.asyncio
async def test_reflector_ignores_unknown_skill_in_hint() -> None:
    """If the reflector hallucinates a non-existent skill, drop the hint
    but keep the verdict."""
    state = _state_with_calls([
        {
            "name": "financial-query", "args": {}, "ok": True,
            "result": {"data": {"datas": [_stock_row("1", "A", 1)]}, "ok": True},
            "error": None, "trace_id": "t",
        }
    ])
    fake = json.dumps({
        "verdict": "need_more",
        "reason": "x", "next_skill_hint": "totally_made_up_skill", "next_args_hint": {"q": "x"},
    })
    with patch("app.agent.nodes.reflector.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake})())
        mock_build.return_value = llm
        out = await reflector_node(state)  # type: ignore[arg-type]
    assert out["reflection_verdict"] == "need_more"
    # Hint must NOT pass through when the skill is unknown.
    assert "next_skill_hint" not in out


# --- router: consume next_skill_hint, skip LLM -----------------------


@pytest.mark.asyncio
async def test_router_consumes_hint_without_calling_llm() -> None:
    """When the state has next_skill_hint pointing to a real enabled
    skill, router should produce a tool_call from the hint and NOT
    call the LLM."""
    state = _state_with_calls(
        calls=[
            {
                "name": "financial-query", "args": {"q": "x"}, "ok": True,
                "result": {"data": {"datas": []}, "ok": True},
                "error": None, "trace_id": "t",
            }
        ],
        next_skill_hint="announcement-search",
        next_args_hint={"query": "贵州茅台 公告", "days": "30"},
    )
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock()
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]

    # LLM must not be called when hint is present.
    llm.ainvoke.assert_not_called()
    new_call = out["tool_calls"][-1]
    assert new_call["name"] == "announcement-search"
    assert new_call["args"]["query"] == "贵州茅台 公告"
    assert new_call["args"]["days"] == "30"
    # Hint is cleared so the next cycle re-evaluates.
    assert out.get("next_skill_hint") is None
    assert out.get("next_args_hint") is None


@pytest.mark.asyncio
async def test_router_falls_back_to_llm_when_no_hint() -> None:
    """No hint → router must consult the LLM as before."""
    fake_response = json.dumps({
        "name": "news-search",
        "args": {"query": "宁德时代 新闻", "limit": "5"},
    })
    state = _state_with_calls(
        calls=[{
            "name": "financial-query", "args": {}, "ok": True,
            "result": {"data": {"datas": []}, "ok": True},
            "error": None, "trace_id": "t",
        }],
    )
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_response})())
        mock_build.return_value = llm
        out = await skill_router_node(state)  # type: ignore[arg-type]
    llm.ainvoke.assert_called_once()
    assert out["tool_calls"][-1]["name"] == "news-search"


@pytest.mark.asyncio
async def test_router_rejects_hint_to_disabled_skill() -> None:
    """If the hinted skill is disabled, router falls through to the
    LLM path instead of using the hint."""
    from app.skills.registry import REGISTRY
    REGISTRY.set_enabled("report-search", False)
    try:
        state = _state_with_calls(
            calls=[{
                "name": "financial-query", "args": {}, "ok": True,
                "result": {"data": {"datas": []}, "ok": True},
                "error": None, "trace_id": "t",
            }],
            next_skill_hint="report-search",
            next_args_hint={"query": "x"},
        )
        fake_response = json.dumps({
            "name": "announcement-search",
            "args": {"query": "x"},
        })
        with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
            llm = AsyncMock()
            llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_response})())
            mock_build.return_value = llm
            out = await skill_router_node(state)  # type: ignore[arg-type]
        llm.ainvoke.assert_called_once()  # fell through
        assert out["tool_calls"][-1]["name"] == "announcement-search"
    finally:
        REGISTRY.set_enabled("report-search", True)


# --- end-to-end multi-step loop (state machine) -----------------------


@pytest.mark.asyncio
async def test_two_step_loop_yields_two_tool_calls() -> None:
    """Walk the full loop: router → executor → reflector → router
    (with hint) → executor → reflector (sufficient) → done."""
    from app.agent.nodes.executor import executor_node

    # Start: empty tool_calls, no hint.
    state: dict = _state_with_calls(
        calls=[],
        reflection_verdict="need_more",  # initial state
        rounds_used=0,
    )
    # Step 1: router picks financial-query via LLM.
    fake_router_1 = json.dumps({
        "name": "financial-query",
        "args": {"query": "今日涨停股票中市值最大的,按总市值排名", "limit": "5"},
    })
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_router_1})())
        mock_build.return_value = llm
        out1 = await skill_router_node(state)  # type: ignore[arg-type]
    state.update(out1)
    assert state["tool_calls"][-1]["name"] == "financial-query"

    # Step 2: executor dispatches financial-query (real handler, real data).
    out2 = await executor_node(state)  # type: ignore[arg-type]
    state.update(out2)
    last = state["tool_calls"][-1]
    assert last["name"] == "financial-query"
    assert last["ok"] is True
    assert last["result"]["data"]["datas"], "financial-query should return non-empty rows in this env"

    # Step 3: reflector decides need_more with hint for announcement-search.
    fake_reflect = json.dumps({
        "verdict": "need_more",
        "reason": "top-1 股票已找到,需要它的最近公告",
        "next_skill_hint": "announcement-search",
        "next_args_hint": {"query": f"{last['result']['data']['datas'][0].get('股票简称', '?')} 最新公告", "days": "30", "limit": "5"},
    })
    with patch("app.agent.nodes.reflector.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=type("R", (), {"content": fake_reflect})())
        mock_build.return_value = llm
        out3 = await reflector_node(state)  # type: ignore[arg-type]
    state.update(out3)
    assert state["reflection_verdict"] == "need_more"
    assert state["next_skill_hint"] == "announcement-search"

    # Step 4: router consumes hint, NO LLM call.
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_build:
        llm = AsyncMock()
        llm.ainvoke = AsyncMock()
        mock_build.return_value = llm
        out4 = await skill_router_node(state)  # type: ignore[arg-type]
    llm.ainvoke.assert_not_called()
    state.update(out4)
    assert state["tool_calls"][-1]["name"] == "announcement-search"
    assert state["next_skill_hint"] is None  # cleared after consumption
