"""LangGraph AgentState and streaming events."""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict


class PlanStep(TypedDict):
    goal: str
    target_skill: str | None
    args: dict[str, Any]
    success_criteria: str


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
    run_id: str
    message_id: str

    # ---- conversation history (pre-loaded) ----
    history: list[dict[str, Any]]
    # Read-only recalled context. It is populated before the graph starts and
    # treated as untrusted user data by every LLM prompt.
    memory_context: str
    # Ephemeral single-run scratchpad; never persisted as long-term memory.
    working_memory: dict[str, Any]

    # ---- agent-internal ----
    plan: list[PlanStep]
    planning_cycle: int
    pending_step_index: int
    # Project-owned SKILL.md/template context loaded for this plan.  It is
    # ephemeral run state, never a user-provided filesystem path.
    loaded_skill_names: list[str]
    loaded_skill_resources: dict[str, dict[str, Any]]
    tool_calls: list[ToolCallRecord]
    reflection: str
    reflection_verdict: Literal["sufficient", "need_more", "failed"]
    # Number of Skills actually dispatched for this user question. This
    # is deliberately distinct from reflection/re-plan rounds: a successful
    # call must still count towards the hard per-question safety cap.
    skill_calls_used: int
    # Router output is explicit so a skipped/invalid plan step cannot fall
    # through to executor and accidentally run the previous tool again.
    router_action: Literal["execute", "continue", "finish"]
    # next_skill_hint / next_args_hint are populated by the reflector
    # when it decides the previous step didn't cover the question and
    # consumed by the skill router on its next turn. They MUST be
    # declared here — LangGraph drops undeclared keys from a
    # TypedDict, which silently breaks the multi-step loop.
    next_skill_hint: str | None
    next_args_hint: dict[str, Any] | None
    policy_notices: list[str]

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
