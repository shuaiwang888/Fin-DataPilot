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
      1. **Reflector's `next_skill_hint`** — if the previous reflection
         emitted a valid hint, use it directly. Handles the
         "I just realized I need to chain" reactive case.
      2. **Plan-driven** — if the planner left a pending step in
         `state.plan`, consume it. This is the "pre-decomposed
         question" fast path that lets the router advance without
         calling the LLM at all.
      3. **LLM fallback** — if neither hint nor plan, ask the LLM
         to pick the next step.
    """
    settings = get_settings()
    previous_results = state.get("tool_calls", [])

    # ---- Loop guard: if the last 2 tool calls have IDENTICAL
    # (name, args) the agent is stuck re-trying the same failing
    # query (LLM keeps re-planning the same way). Bail with an
    # honest "I don't have the right data" answer instead of
    # burning the recursion budget.
    if len(previous_results) >= 2:
        last = previous_results[-1]
        prev = previous_results[-2]
        if (
            last.get("name") == prev.get("name")
            and json.dumps(last.get("args", {}), sort_keys=True, ensure_ascii=False)
            == json.dumps(prev.get("args", {}), sort_keys=True, ensure_ascii=False)
        ):
            logger.warning(
                "router: detected identical-args loop on %s; bailing",
                last.get("name"),
            )
            return {
                "final_answer": (
                    f"已对 `{last.get('name')}` 重复调用了相同的查询，均未拿到有效数据。"
                    "请换个问法：比如直接给出要查的股票名 / 代码，或者把条件说得更具体一些。"
                ),
                "reflection_verdict": "failed",
                "error": f"identical-args loop on {last.get('name')}",
            }

    # ---- Fast path #1: consume reflector's next_skill_hint ----
    hint_skill = state.get("next_skill_hint")
    hint_args = state.get("next_args_hint")
    if (
        hint_skill
        and isinstance(hint_skill, str)
        and REGISTRY.get_spec(hint_skill)
        and REGISTRY.is_enabled(hint_skill)
        and isinstance(hint_args, dict)
    ):
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
            "next_skill_hint": None,
            "next_args_hint": None,
        }

    # ---- Fast path #2: consume the next plan step ----
    plan = state.get("plan") or []
    pending_idx = state.get("pending_step_index", 0)
    if plan and pending_idx < len(plan):
        step = plan[pending_idx]
        skill = step.get("target_skill")
        # A null skill (whether planner fallback or explicit
        # "summarise" step) is treated as "let the LLM router decide
        # what to do next". We still advance the index so we don't
        # re-encounter the same null step on the next turn. This is
        # safer than the old behaviour of immediately emitting a
        # final-answer placeholder, which bailed out before any skill
        # ran.
        if skill is None:
            logger.info(
                "router: plan step %d has null target_skill (goal=%r); falling through to LLM path",
                pending_idx, step.get("goal", ""),
            )
            return {
                "pending_step_index": pending_idx + 1,
                # Don't reset plan — other valid steps may follow.
            }
        if not REGISTRY.get_spec(skill) or not REGISTRY.is_enabled(skill):
            logger.warning("router: plan step %d references invalid skill %r, skipping", pending_idx, skill)
            return {
                "pending_step_index": pending_idx + 1,
            }
        args = _substitute_placeholders(
            step.get("args", {}),
            previous_results,
        )
        return {
            "pending_step_index": pending_idx + 1,
            "tool_calls": previous_results + [
                {
                    "name": skill,
                    "args": args,
                    "trace_id": "",
                    "result": None,
                    "ok": False,
                    "duration_ms": 0,
                    "error": None,
                }
            ],
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


# ---- Plan placeholder substitution ------------------------------------


def _substitute_placeholders(args: dict[str, Any], prior_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace `<step_N_xxx>` placeholders in args with values from
    the Nth prior call's result.

    Supported placeholders:
      <step_N_top_stock>   → "name(code)" of the top market-cap row in
                              step N's result (or top change% for
                              "涨停" patterns)
      <step_N_top_name>    → just the name
      <step_N_top_code>    → just the code
      <step_N_first>       → the first row, JSON-serialised
    """
    if not prior_calls:
        return args

    pattern = re.compile(r"<step_(\d+)_(top_stock|top_name|top_code|first)>")

    def lookup(step_idx: int, key: str) -> str:
        if step_idx >= len(prior_calls):
            return ""
        call = prior_calls[step_idx]
        data = (call.get("result") or {}).get("data") or {}
        rows: list[dict[str, Any]] = []
        if isinstance(data, dict):
            rows = data.get("datas") or data.get("articles") or data.get("announcements") or data.get("reports") or []
        if not isinstance(rows, list) or not rows:
            return ""
        # Pick the row with the highest market cap (or first by default).
        def _num(r: dict) -> float:
            for k in ("总市值", "A股市值", "总市值(亿元)", "market_cap"):
                v = r.get(k)
                if v is None:
                    continue
                try:
                    return float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    continue
            return 0.0
        rows_sorted = sorted(rows, key=_num, reverse=True)
        top = rows_sorted[0]
        name = top.get("股票简称") or top.get("name") or top.get("简称") or ""
        code = top.get("股票代码") or top.get("code") or top.get("代码") or ""
        if key == "top_stock":
            return f"{name} {code}".strip()
        if key == "top_name":
            return str(name)
        if key == "top_code":
            return str(code)
        if key == "first":
            return json.dumps(top, ensure_ascii=False)
        return ""

    def replace(match: re.Match) -> str:
        step_idx = int(match.group(1))
        key = match.group(2)
        return lookup(step_idx, key)

    def walk(obj: Any) -> Any:
        if isinstance(obj, str):
            return pattern.sub(replace, obj)
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj

    return walk(args)
