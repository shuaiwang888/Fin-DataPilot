"""Load project-owned Skill instructions/templates for the current plan."""

from __future__ import annotations

from typing import Any

from app.agent.state import AgentState
from app.config import get_settings
from app.skills.registry import REGISTRY
from app.skills.resources import load_skill_resources


async def skill_context_loader_node(state: AgentState) -> dict[str, Any]:
    """Materialize resources for every enabled capability named by the plan.

    The loader runs after every initial plan and re-plan.  It is intentionally a
    separate graph node so the SSE trace makes the capability-loading phase
    visible instead of making tool execution look magical.
    """
    requested = [
        str(step.get("target_skill"))
        for step in state.get("plan", [])
        if step.get("target_skill")
    ]
    # A reflector can name a follow-up capability after a partial result.  Load
    # it too, even before the next re-plan consumes the hint.
    hint = state.get("next_skill_hint")
    if isinstance(hint, str):
        requested.append(hint)
    enabled = [
        name for name in dict.fromkeys(requested)
        if REGISTRY.get_spec(name) and REGISTRY.is_enabled(name)
    ]
    resources = load_skill_resources(
        enabled, max_chars=get_settings().agent_skill_resource_max_chars
    )
    return {
        "loaded_skill_names": list(resources),
        "loaded_skill_resources": resources,
    }
