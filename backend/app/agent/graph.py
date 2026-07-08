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
from typing import Any, AsyncIterator

from langgraph.graph import END, StateGraph

from app.agent.nodes.executor import executor_node
from app.agent.nodes.planner import planner_node
from app.agent.nodes.reflector import reflector_node
from app.agent.nodes.skill_router import skill_router_node
from app.agent.nodes.synthesizer import synthesize
from app.agent.state import AgentState, EV_DONE, EV_ERROR
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

    # After router: if final_answer was set, go to synthesizer; else go to executor
    def _after_router(state: AgentState) -> str:
        if state.get("error"):
            return "synthesizer"
        if state.get("final_answer"):
            return "synthesizer"
        return "executor"

    g.add_conditional_edges("skill_router", _after_router, {
        "executor": "executor",
        "synthesizer": "synthesizer",
    })

    # After executor: always go to reflector
    g.add_edge("executor", "reflector")

    # After reflector: three paths.
    #   need_more + plan still has steps → skill_router (advance plan)
    #   need_more + plan exhausted (cleared by reflector) → planner (re-plan)
    #   sufficient / failed / rounds cap hit → synthesizer
    def _after_reflector(state: AgentState) -> str:
        verdict = state.get("reflection_verdict", "sufficient")
        rounds = state.get("rounds_used", 0)
        max_rounds = get_settings().agent_max_reflect_rounds
        if verdict == "need_more" and rounds < max_rounds:
            plan = state.get("plan") or []
            if not plan:
                # Plan was cleared (exhausted) → re-plan
                return "planner"
            return "skill_router"
        return "synthesizer"

    g.add_conditional_edges("reflector", _after_reflector, {
        "planner": "planner",
        "skill_router": "skill_router",
        "synthesizer": "synthesizer",
    })

    g.add_edge("synthesizer", END)

    # Default LangGraph recursion limit is 25. With the multi-step
    # plan + re-plan flow + anysearch being a slightly slower skill,
    # we hit it on complex questions. Pass the limit via the config
    # dict at astream time (LangGraph 0.x's .compile() doesn't accept
    # a recursion_limit kwarg; the config goes on astream/ainvoke).
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
) -> AsyncIterator[dict[str, Any]]:
    """Stream agent events for a single user turn."""
    from app.agent.state import (
        EV_MESSAGE_FINAL,
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
        "history": history,
        "tool_calls": [],
        "rounds_used": 0,
        "reflection_verdict": "need_more",
        "trace_id": trace_id,
        "plan": [],
        "pending_step_index": 0,
        "next_skill_hint": None,
        "next_args_hint": None,
    }
    yield {"event": EV_THINK, "data": {"step": "entry", "text": f"开始处理：{user_query}", "trace_id": trace_id}}

    graph = get_graph()
    final_state: AgentState = dict(init_state)

    try:
        # Bump LangGraph's default 25 recursion limit to 50 for
        # multi-step plan + re-plan flows. Most queries stay <10.
        async for event in graph.astream(init_state, config={"recursion_limit": 50}):
            # event is dict {node_name: node_output}
            for node_name, node_out in event.items():
                if not isinstance(node_out, dict):
                    continue
                final_state.update(node_out)
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
                            "text": f"已规划 {len(plan)} 步：{' → '.join(steps)}" + (
                                f"\n理由：{rationale}" if rationale else ""
                            ),
                        },
                    }
                if node_name == "skill_router":
                    tc = (node_out.get("tool_calls") or [])
                    if tc and tc[-1].get("result") is None:
                        last = tc[-1]
                        yield {
                            "event": EV_TOOL_CALL,
                            "data": {
                                "name": last["name"],
                                "args": last.get("args", {}),
                                "trace_id": last.get("trace_id", ""),
                            },
                        }
                    if node_out.get("final_answer"):
                        yield {
                            "event": EV_THINK,
                            "data": {"step": "router_final", "text": "直接生成最终答案"},
                        }
                elif node_name == "executor":
                    tc = (node_out.get("tool_calls") or [])
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
        yield {"event": EV_ERROR, "data": {"message": f"Agent 执行失败: {exc}", "trace_id": trace_id}}

    # If the router produced a final answer directly (no synthesizer streaming)
    if final_state.get("final_answer") and not any(
        True for _ in []
    ):  # placeholder check
        yield {
            "event": EV_MESSAGE_FINAL,
            "data": {
                "content": final_state["final_answer"],
                "tool_calls": final_state.get("tool_calls", []),
            },
        }
    else:
        # Stream synthesizer output
        async for ev in synthesize(final_state):
            yield ev

    yield {"event": EV_DONE, "data": {"trace_id": trace_id}}
