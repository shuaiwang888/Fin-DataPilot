"""LangGraph StateGraph assembly + streaming entry point.

Pipeline:

    planner → skill_router → executor → reflector ─┐
       ↑        ↑            ↓                       │
       │        │            └── reflects: enough?  │
       │        │                                   │
       │        └──── hint: skip LLM, use plan step │
       │                                            │
       └────────── plan exhausted + need_more ──────┘
                            │
                            ↓
                       synthesizer → END

The planner runs once at the start to pre-decompose the question
into a sequence of plan steps. The skill router then walks through
the plan without re-asking the LLM, with the reflector deciding
when to stop or when to trigger a re-plan.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, cast

from langgraph.graph import END, StateGraph

from app.agent.nodes.executor import executor_node
from app.agent.nodes.planner import planner_node
from app.agent.nodes.reflector import reflector_node
from app.agent.nodes.skill_router import skill_router_node
from app.agent.nodes.synthesizer import synthesize
from app.agent.state import EV_DONE, EV_ERROR, AgentState
from app.config import get_settings
from app.skills.registry import REGISTRY

logger = logging.getLogger(__name__)


def _build_graph() -> Any:
    g = StateGraph(AgentState)
    g.add_node("planner", planner_node)
    g.add_node("skill_router", skill_router_node)
    g.add_node("executor", executor_node)
    g.add_node("reflector", reflector_node)
    g.add_node("synthesizer", lambda s: s)  # placeholder; streaming handled outside

    g.set_entry_point("planner")
    g.add_edge("planner", "skill_router")  # planner always feeds into the router

    # Router explicitly says whether it scheduled a fresh tool call, skipped a
    # plan step, or finished. Never infer this from `tool_calls`: doing so made
    # a skipped step execute the last completed tool for a second time.
    def _after_router(state: AgentState) -> str:
        action = state.get("router_action", "finish")
        if action == "execute":
            return "executor"
        if action == "continue":
            return "skill_router"
        return "synthesizer"

    g.add_conditional_edges(
        "skill_router",
        _after_router,
        {
            "executor": "executor",
            "skill_router": "skill_router",
            "synthesizer": "synthesizer",
        },
    )

    # After executor: exactly one reflection takes place for each Skill
    # response. The reflector decides whether the next decomposed sub-task is
    # necessary before the router can schedule it.
    g.add_edge("executor", "reflector")

    # After reflector: continue only while the hard Skill-call budget remains.
    def _after_reflector(state: AgentState) -> str:
        verdict = state.get("reflection_verdict", "sufficient")
        calls_used = state.get("skill_calls_used", len(state.get("tool_calls", [])))
        max_calls = get_settings().agent_max_skill_calls
        if verdict == "need_more" and calls_used < max_calls:
            plan = state.get("plan") or []
            if not plan:
                # Plan was cleared (exhausted) → re-plan with all evidence.
                return "planner"
            return "skill_router"
        return "synthesizer"

    g.add_conditional_edges(
        "reflector",
        _after_reflector,
        {
            "planner": "planner",
            "skill_router": "skill_router",
            "synthesizer": "synthesizer",
        },
    )

    g.add_edge("synthesizer", END)

    # Default LangGraph recursion limit is 25. A full 8-call flow can use
    # planner/router/executor/reflector nodes multiple times, so reserve room
    # for one re-plan without allowing unbounded tool work.
    return g.compile()


_GRAPH = None


def get_graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


# ---------- Public streaming entry point ----------


async def run_agent_stream(
    user_query: str,
    history: list[dict[str, Any]],
    session_id: str,
    run_id: str | None = None,
    memory_context: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Stream agent events for a single user turn."""
    from app.agent.state import (
        EV_REFLECTION,
        EV_THINK,
        EV_TOOL_CALL,
        EV_TOOL_RESULT,
    )
    from app.utils.trace import generate_trace_id

    if not REGISTRY.list_specs():
        yield {"event": EV_ERROR, "data": {"message": "No skills registered"}}
        yield {"event": EV_DONE, "data": {}}
        return

    trace_id = generate_trace_id()
    init_state: AgentState = {
        "user_query": user_query,
        "session_id": session_id,
        "run_id": run_id or trace_id,
        "history": history,
        "memory_context": memory_context,
        "working_memory": {
            "query": user_query,
            "completed_steps": [],
            "tool_evidence": [],
        },
        "tool_calls": [],
        "skill_calls_used": 0,
        "reflection_verdict": "need_more",
        "trace_id": trace_id,
        "plan": [],
        "pending_step_index": 0,
        "next_skill_hint": None,
        "next_args_hint": None,
        "router_action": "continue",
    }
    yield {
        "event": EV_THINK,
        "data": {"step": "entry", "text": f"开始处理：{user_query}", "trace_id": trace_id},
    }

    graph = get_graph()
    final_state = cast(AgentState, dict(init_state))

    try:
        async for event in graph.astream(
            init_state,
            config={"recursion_limit": 50, "configurable": {"thread_id": run_id or trace_id}},
        ):
            # event is dict {node_name: node_output}
            for node_name, node_out in event.items():
                if not isinstance(node_out, dict):
                    continue
                final_state.update(cast(AgentState, node_out))
                # Stream per-node events
                if node_name == "planner":
                    plan = node_out.get("plan") or []
                    steps = [
                        f"{s.get('target_skill') or 'final'} ({s.get('goal', '')[:40]})"
                        for s in plan
                    ]
                    rationale = node_out.get("rationale", "") or ""
                    yield {
                        "event": EV_THINK,
                        "data": {
                            "step": "plan",
                            "text": f"已规划 {len(plan)} 步：{' → '.join(steps)}"
                            + (f"\n理由：{rationale}" if rationale else ""),
                        },
                    }
                if node_name == "skill_router":
                    tc = node_out.get("tool_calls") or []
                    if node_out.get("router_action") == "execute" and tc:
                        last = tc[-1]
                        yield {
                            "event": EV_TOOL_CALL,
                            "data": {
                                "name": last["name"],
                                "args": last.get("args", {}),
                                "trace_id": last.get("trace_id", ""),
                            },
                        }
                    if node_out.get("router_action") == "finish":
                        yield {
                            "event": EV_THINK,
                            "data": {"step": "finalize", "text": "数据收集完成，正在汇总最终回答"},
                        }
                elif node_name == "executor":
                    tc = node_out.get("tool_calls") or []
                    if tc:
                        last = tc[-1]
                        yield {
                            "event": EV_TOOL_RESULT,
                            "data": {
                                "name": last["name"],
                                "ok": last.get("ok", False),
                                "duration_ms": last.get("duration_ms", 0),
                                "trace_id": last.get("trace_id", ""),
                                "result": last.get("result"),
                                "error": last.get("error"),
                            },
                        }
                elif node_name == "reflector":
                    yield {
                        "event": EV_REFLECTION,
                        "data": {
                            "verdict": node_out.get("reflection_verdict", "sufficient"),
                            "reason": node_out.get("reflection", ""),
                        },
                    }
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent graph execution failed")
        yield {
            "event": EV_ERROR,
            "data": {"message": f"Agent 执行失败: {exc}", "trace_id": trace_id},
        }

    # All exits, including router guards and failures, go through the one
    # synthesizer. This keeps user-facing content separate from execution
    # trace and ensures exactly one final-answer event per turn.
    async for ev in synthesize(final_state):
        yield ev

    yield {"event": EV_DONE, "data": {"trace_id": trace_id}}
