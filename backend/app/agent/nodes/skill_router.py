"""Skill router node: ask the LLM which skill to call next (or stop)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.prompts.system import render_system_prompt
from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model
from app.skills.registry import REGISTRY

logger = logging.getLogger(__name__)


def _try_parse_tool_call(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a tool_call JSON from LLM output."""
    text = text.strip()
    # Direct JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "name" in obj and "args" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    # First {...} block
    m = re.search(r"\{[^{}]*\"name\"[^{}]*\"args\"[^{}]*\{.*?\}\s*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def skill_router_node(state: AgentState) -> dict[str, Any]:
    """Decide the next skill to call, or terminate if the answer is ready.

    Routing priority:
      1. If the previous reflector step emitted `next_skill_hint` + a
         valid `next_args_hint` AND the named skill is registered +
         enabled, use the hint directly. This is the multi-step
         "got a list of 56 stocks, now go get announcement for the
         top one" loop — reflector knows the original question, we
         trust it.
      2. Otherwise fall back to the LLM call. The LLM sees the full
         multi-call history and decides the next tool_call.
    """
    settings = get_settings()
    previous_results = state.get("tool_calls", [])

    # ---- Fast path: consume reflector's next_skill_hint ----
    hint_skill = state.get("next_skill_hint")
    hint_args = state.get("next_args_hint")
    if (
        hint_skill
        and isinstance(hint_skill, str)
        and REGISTRY.get_spec(hint_skill)
        and REGISTRY.is_enabled(hint_skill)
        and isinstance(hint_args, dict)
    ):
        # The hint is only consumed ONCE — clear it so the next
        # reflector→router cycle re-evaluates from scratch.
        return {
            "pending_step_index": state.get("pending_step_index", 0),
            "tool_calls": previous_results + [
                {
                    "name": hint_skill,
                    "args": hint_args,
                    "trace_id": "",
                    "result": None,
                    "ok": False,
                    "duration_ms": 0,
                    "error": None,
                }
            ],
            # Clear the consumed hint so the next reflection cycle
            # starts fresh.
            "next_skill_hint": None,
            "next_args_hint": None,
        }

    # ---- LLM path ----
    llm = build_chat_model(settings, temperature=0.0)

    history = state.get("history", [])
    history_text = "\n".join(
        f"[{m['role']}] {m['content']}" for m in history[-10:]
    )

    user_query = state.get("user_query", "")
    rounds = state.get("rounds_used", 0)

    user_prompt = (
        f"对话历史（最近 10 条）：\n{history_text or '（无）'}\n\n"
        f"用户最新问题：{user_query}\n\n"
        f"已完成的工具调用：{len(previous_results)} 次\n"
        f"反思轮数：{rounds}/{settings.agent_max_reflect_rounds}\n\n"
        "请按 system prompt 中的契约，输出下一步的 tool_call JSON，或者直接输出最终答案。"
    )

    try:
        resp = await llm.ainvoke(
            [SystemMessage(content=render_system_prompt()), HumanMessage(content=user_prompt)]
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("skill_router LLM call failed")
        return {
            "reflection_verdict": "failed",
            "error": f"LLM call failed: {exc}",
        }

    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    parsed = _try_parse_tool_call(content)

    if parsed is None:
        # Treat as final answer
        return {
            "final_answer": content,
            "reflection_verdict": "sufficient",
        }

    name = parsed.get("name", "")
    args = parsed.get("args", {}) or {}

    if not REGISTRY.get_spec(name):
        return {
            "reflection_verdict": "failed",
            "error": f"LLM requested unknown skill: {name}",
            "final_answer": f"抱歉，AI 选择的工具 `{name}` 不存在或已禁用。请换个问法或启用对应 Skill。",
        }
    if not REGISTRY.is_enabled(name):
        return {
            "reflection_verdict": "failed",
            "error": f"LLM requested disabled skill: {name}",
            "final_answer": f"抱歉，工具 `{name}` 当前已被禁用。请在前端 Skill 管理中启用后再试。",
        }

    return {
        "pending_step_index": state.get("pending_step_index", 0),
        "tool_calls": previous_results + [
            {
                "name": name,
                "args": args,
                "trace_id": "",
                "result": None,
                "ok": False,
                "duration_ms": 0,
                "error": None,
            }
        ],
    }
