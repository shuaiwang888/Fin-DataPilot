"""Integration test: multi-step loop via the real LangGraph graph.

Catches the regression where the reflector's next_skill_hint was being
dropped because it wasn't declared on AgentState. The router and
reflector unit tests both pass when the hint is passed in directly,
but LangGraph drops undeclared TypedDict fields — this test goes
through the actual graph to confirm the hint survives the round trip.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.graph import get_graph


def _financial_query_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "result": {
            "data": {"datas": rows, "code_count": len(rows)},
            "ok": True,
            "trace_id": "t_test",
            "duration_ms": 10,
        },
    }


def _announcement_result() -> dict[str, Any]:
    return {
        "ok": True,
        "result": {
            "data": {
                "announcements": [
                    {"title": "重大事项公告", "date": "2026-07-01", "summary": "test"},
                ],
                "count": 1,
            },
            "ok": True,
            "trace_id": "t_ann",
            "duration_ms": 10,
        },
    }


@pytest.mark.asyncio
async def test_graph_propagates_reflector_hint_to_router() -> None:
    """The whole point: walk the graph with stubbed LLM and real
    executor, and assert that the second tool call is the one the
    reflector hinted at (not whatever the LLM router decides)."""
    # ---- Stub the LLM ----
    initial_query = json.dumps({
        "name": "financial-query",
        "args": {"query": "今日A股涨停股票,按总市值降序排序", "limit": "5"},
    })
    # After the deterministic-pattern reflector fires (no LLM), the
    # router fast-paths to announcement-search (no LLM). The executor
    # then runs announcement-search, which has no followup pattern,
    # so the reflector LLM IS called for the post-announcement check.
    # Stub it to return sufficient so the loop terminates.
    sufficient_response = json.dumps({"verdict": "sufficient", "reason": "test terminate"})
    # Pre-populate the plan so the planner LLM doesn't run — we want
    # to test the rest of the graph (router/reflector/hint) with a
    # deterministic plan in place.
    pre_plan = [
        {"goal": "find top stock", "target_skill": "financial-query",
         "args": {"query": "今日A股涨停股票,按总市值降序排序", "limit": "5"}},
        {"goal": "get announcements", "target_skill": "announcement-search",
         "args": {"query": "<step_0_top_stock> 最近公告", "days": "30", "limit": "10"}},
    ]
    planner_response = json.dumps({"plan": pre_plan, "rationale": "test plan"})

    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_router_build, \
         patch("app.agent.nodes.reflector.build_chat_model") as mock_refl_build, \
         patch("app.agent.nodes.planner.build_chat_model") as mock_planner_build, \
         patch("app.agent.nodes.executor.REGISTRY") as mock_executor_reg:
        router_llm = AsyncMock()
        router_llm.ainvoke = AsyncMock(return_value=type(
            "R", (), {"content": initial_query}
        )())
        mock_router_build.return_value = router_llm
        refl_llm = AsyncMock()
        refl_llm.ainvoke = AsyncMock(return_value=type(
            "R", (), {"content": sufficient_response}
        )())
        mock_refl_build.return_value = refl_llm
        planner_llm = AsyncMock()
        planner_llm.ainvoke = AsyncMock(return_value=type(
            "R", (), {"content": planner_response}
        )())
        mock_planner_build.return_value = planner_llm

        async def fake_dispatch(name: str, args: dict[str, Any]):
            from app.skills.base import ToolResult
            if name == "financial-query":
                return ToolResult(
                    tool=name, ok=True,
                    data={
                        "datas": [
                            {"股票代码": "603296.SH", "股票简称": "华勤技术", "总市值": 1169.57, "涨跌幅": 10.0},
                            {"股票代码": "601318.SH", "股票简称": "中国平安", "总市值": 1100.0, "涨跌幅": 9.9},
                        ],
                        "code_count": 56,
                    },
                )
            if name == "announcement-search":
                return ToolResult(
                    tool=name, ok=True,
                    data={"announcements": [{"title": "公告A", "summary": "内容"}], "count": 1},
                )
            return ToolResult(tool=name, ok=False, error="not stubbed")
        mock_executor_reg.dispatch = fake_dispatch

        graph = get_graph()
        init = {
            "user_query": "涨停的股票中市值最大的那只最近的公告或研报",
            "session_id": "s_test",
            "message_id": "",
            "history": [],
            "tool_calls": [],
            "rounds_used": 0,
            "reflection_verdict": "need_more",
            "trace_id": "t_init",
            "plan": [],
            "pending_step_index": 0,
            "reflection": "",
            "final_answer": "",
            "error": None,
            "next_skill_hint": None,
            "next_args_hint": None,
        }
        final_state: dict[str, Any] = dict(init)
        async for ev in graph.astream(init, config={"recursion_limit": 50}):
            for _, node_out in ev.items():
                if isinstance(node_out, dict):
                    final_state.update(node_out)

    # ---- The KEY assertion: step 2 must be announcement-search ----
    calls = final_state.get("tool_calls", [])
    names = [c.get("name") for c in calls]
    assert names[0] == "financial-query", f"step 1 wrong: {names}"
    assert names[1] == "announcement-search", (
        f"step 2 should follow the plan's 2nd step, got {names[1]!r}. "
        f"Full calls: {calls!r}"
    )
    # The plan should have substituted the <step_0_top_stock> with
    # the top row from step 0 (华勤技术 603296.SH).
    ann_call = calls[1]
    assert "华勤技术" in ann_call["args"].get("query", "")
    # Planner LLM was called once (initial).
    assert planner_llm.ainvoke.call_count == 1
    # Router LLM should not have been called (plan-driven path).
    assert router_llm.ainvoke.call_count == 0, (
        f"router LLM called {router_llm.ainvoke.call_count} times; "
        f"expected 0. Plan-driven router should not call LLM."
    )


@pytest.mark.asyncio
async def test_graph_executes_report_and_announcement_for_and_query() -> None:
    """Regression for "今日涨停 + 市值最大 + 公告和研报".

    The planner may under-plan and only include announcement-search.
    The normalized plan plus deterministic reflector should still execute:
    financial-query → report-search → announcement-search.
    """
    underplanned = [
        {"goal": "find top stock", "target_skill": "financial-query",
         "args": {"query": "今日A股涨停股票,按总市值降序排序", "limit": "5"}},
        {"goal": "get announcements", "target_skill": "announcement-search",
         "args": {"query": "<step_0_top_name>的公告", "days": "30", "limit": "10"}},
    ]
    planner_response = json.dumps({"plan": underplanned, "rationale": "underplanned"})
    sufficient_response = json.dumps({"verdict": "sufficient", "reason": "all covered"})

    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_router_build, \
         patch("app.agent.nodes.reflector.build_chat_model") as mock_refl_build, \
         patch("app.agent.nodes.planner.build_chat_model") as mock_planner_build, \
         patch("app.agent.nodes.executor.REGISTRY") as mock_executor_reg:
        router_llm = AsyncMock()
        router_llm.ainvoke = AsyncMock()
        mock_router_build.return_value = router_llm
        refl_llm = AsyncMock()
        refl_llm.ainvoke = AsyncMock(return_value=type(
            "R", (), {"content": sufficient_response}
        )())
        mock_refl_build.return_value = refl_llm
        planner_llm = AsyncMock()
        planner_llm.ainvoke = AsyncMock(return_value=type(
            "R", (), {"content": planner_response}
        )())
        mock_planner_build.return_value = planner_llm

        async def fake_dispatch(name: str, args: dict[str, Any]):
            from app.skills.base import ToolResult
            if name == "financial-query":
                return ToolResult(
                    tool=name, ok=True,
                    data={
                        "datas": [
                            {"股票代码": "603296.SH", "股票简称": "华勤技术", "总市值": 1169.57, "涨跌幅": 10.0},
                        ],
                        "code_count": 1,
                    },
                )
            if name == "report-search":
                return ToolResult(
                    tool=name, ok=True,
                    data={"reports": [{"title": "研报A", "summary": "内容"}], "count": 1},
                )
            if name == "announcement-search":
                return ToolResult(
                    tool=name, ok=True,
                    data={"announcements": [{"title": "公告A", "author": "admin"}], "count": 1},
                )
            return ToolResult(tool=name, ok=False, error="not stubbed")
        mock_executor_reg.dispatch = fake_dispatch

        graph = get_graph()
        init = {
            "user_query": "给出今日涨停的股票中，市值最大的那只股票，近期的公告和研报",
            "session_id": "s_test",
            "message_id": "",
            "history": [],
            "tool_calls": [],
            "rounds_used": 0,
            "reflection_verdict": "need_more",
            "trace_id": "t_init",
            "plan": [],
            "pending_step_index": 0,
            "reflection": "",
            "final_answer": "",
            "error": None,
            "next_skill_hint": None,
            "next_args_hint": None,
        }
        final_state: dict[str, Any] = dict(init)
        async for ev in graph.astream(init, config={"recursion_limit": 50}):
            for _, node_out in ev.items():
                if isinstance(node_out, dict):
                    final_state.update(node_out)

    calls = final_state.get("tool_calls", [])
    assert [c.get("name") for c in calls] == [
        "financial-query",
        "report-search",
        "announcement-search",
    ]
    assert calls[1]["args"]["query"] == "华勤技术的研报"
    assert calls[2]["args"]["query"] == "华勤技术的公告"
    assert "admin" not in calls[1]["args"]["query"]
    assert "admin" not in calls[2]["args"]["query"]
    router_llm.ainvoke.assert_not_called()
