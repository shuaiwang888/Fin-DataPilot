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
    with patch("app.agent.nodes.skill_router.build_chat_model") as mock_router_build, \
         patch("app.agent.nodes.reflector.build_chat_model") as mock_refl_build, \
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
        async for ev in graph.astream(init):
            for _, node_out in ev.items():
                if isinstance(node_out, dict):
                    final_state.update(node_out)

    # ---- The KEY assertion: step 2 must be announcement-search ----
    calls = final_state.get("tool_calls", [])
    names = [c.get("name") for c in calls]
    assert names[0] == "financial-query", f"step 1 wrong: {names}"
    assert names[1] == "announcement-search", (
        f"step 2 should follow reflector's hint, got {names[1]!r}. "
        f"This means the next_skill_hint was dropped — likely an "
        f"undeclared AgentState field. Full calls: {calls!r}"
    )
    # The hint must also be the right one for 华勤技术 (top-1 by
    # market cap, which is 1169.57 in the stub).
    ann_call = calls[1]
    assert "华勤技术" in ann_call["args"].get("query", "")
    # Router LLM should have been called exactly once (initial).
    # Step 2 should consume the hint, not call the LLM.
    assert router_llm.ainvoke.call_count == 1, (
        f"router LLM called {router_llm.ainvoke.call_count} times; "
        f"expected 1 (initial). Subsequent calls should use the hint."
    )
