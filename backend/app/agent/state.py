"""LangGraph AgentState and streaming events."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from typing_extensions import TypedDict


class PlanStep(TypedDict):
    goal: str
    target_skill: str | None
    args: dict[str, Any]


class ToolCallRecord(TypedDict):
    name: str
    args: dict[str, Any]
    trace_id: str
    result: dict[str, Any] | None
    ok: bool
    duration_ms: int
    error: str | None


class AgentState(TypedDict, total=False):
    # ---- inputs ----
    user_query: str
    session_id: str
    message_id: str

    # ---- conversation history (pre-loaded) ----
    history: list[dict[str, Any]]

    # ---- agent-internal ----
    plan: list[PlanStep]
    pending_step_index: int
    tool_calls: list[ToolCallRecord]
    reflection: str
    reflection_verdict: Literal["sufficient", "need_more", "failed"]
    rounds_used: int

    # ---- outputs ----
    final_answer: str
    error: str | None
    trace_id: str


# Event vocabulary streamed to the client over SSE
EV_PING = "ping"
EV_SESSION = "session"
EV_THINK = "think"
EV_PLAN = "plan"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_REFLECTION = "reflection"
EV_SUMMARY_START = "summary_start"
EV_TOKEN_DELTA = "token_delta"
EV_MESSAGE_FINAL = "message_final"
EV_ERROR = "error"
EV_DONE = "done"
