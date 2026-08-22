"""Executor node: invoke the Skill requested by skill_router and record the result."""

from __future__ import annotations

import logging
from typing import Any

from app.agent.state import AgentState
from app.agent.policy import assess_tool_call
from app.skills.base import ToolResult
from app.skills.configuration import refresh_published_skill_configuration
from app.skills.registry import REGISTRY
from app.storage.repository import is_agent_run_cancel_requested_async, save_tool_run_async
from app.utils.trace import generate_trace_id

logger = logging.getLogger(__name__)


async def executor_node(state: AgentState) -> dict[str, Any]:
    """Execute the most recent tool call recorded in state."""
    calls = list(state.get("tool_calls", []))
    if not calls:
        return {"error": "executor called with no tool_calls"}

    pending = calls[-1]
    name = pending["name"]
    args = pending.get("args", {}) or {}
    # The router assigns the trace id before emitting `tool_call`; retain it
    # so the subsequent `tool_result` can be paired by the client. Keep the
    # fallback for direct executor tests/legacy callers.
    trace_id = pending.get("trace_id") or generate_trace_id()
    pending["trace_id"] = trace_id

    if await is_agent_run_cancel_requested_async(state.get("run_id")):
        result = ToolResult(
            tool=name,
            ok=False,
            error="本次任务已被用户取消。",
            trace_id=trace_id,
            meta={"error_code": "RUN_CANCELLED", "cancelled": True},
        )
    else:
        await refresh_published_skill_configuration()
        decision = assess_tool_call(name, args)
        if not decision.allowed:
            result = ToolResult(
                tool=name,
                ok=False,
                error=decision.message,
                trace_id=trace_id,
                meta={"error_code": decision.code, "policy_blocked": True},
            )
        else:
            logger.info("[%s] dispatching skill %s args=%s", trace_id, name, args)
            result = await REGISTRY.dispatch(name, args)

    pending["result"] = result.to_dict() if result.ok else None
    pending["ok"] = result.ok
    pending["duration_ms"] = result.duration_ms
    pending["error"] = result.error
    try:
        await save_tool_run_async(
            session_id=state.get("session_id"),
            skill_name=name,
            args=args,
            result=result.to_dict(),
            ok=result.ok,
            duration_ms=result.duration_ms,
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("[%s] unable to persist tool audit record", trace_id)

    # Single-run working memory is deliberately ephemeral. It gives later
    # nodes a compact ledger without writing raw tool evidence into the
    # user's long-term profile.
    working_memory = dict(state.get("working_memory", {}))
    evidence = list(working_memory.get("tool_evidence", []))
    evidence.append(
        {
            "name": name,
            "args": args,
            "ok": result.ok,
            "trace_id": trace_id,
        }
    )
    working_memory["tool_evidence"] = evidence
    completed = list(working_memory.get("completed_steps", []))
    completed.append(name)
    working_memory["completed_steps"] = completed

    return {
        "tool_calls": calls,
        "skill_calls_used": state.get("skill_calls_used", 0) + 1,
        "working_memory": working_memory,
    }
