"""Planner node: pre-decompose the user question into a multi-step plan.

Pipeline:
    planner → router → executor → reflector → (need_more)
              ↑         ↓
              └─────────┘ advance through plan / replan if exhausted
              ↓
           synthesizer

The planner LLM call sees the full question + the available skill list
and outputs a plan: a list of {goal, target_skill, args} steps to
execute in order. The skill router then walks the plan step by step
without re-asking the LLM, which is both faster and more coherent
than the original "decide next, execute, decide next" loop.

The reflector can still trigger a re-plan (by clearing the plan state)
when the current plan is exhausted and a follow-up is needed. This
combines the best of both worlds: explicit upfront planning + reactive
re-planning on unexpected outcomes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model
from app.skills.registry import REGISTRY

logger = logging.getLogger(__name__)


PLANNER_PROMPT = """你是 Fin-DataPilot 的规划器（Planner）。基于用户的最新问题，**预先**把它拆成"一步步执行"的具体子任务，输出一个执行计划。

# 输入
- 用户的最新问题
- 可用 Skill 列表（带每个 skill 的参数 schema 摘要）

# 输出（严格 JSON，不带 markdown 代码块）
{{
  "plan": [
    {{"goal": "<这一步要达成什么目标>", "target_skill": "<skill 名或 null>", "args": {{...}}}}
  ],
  "rationale": "<简短解释为什么这么拆>"
}}

# 规则
1. **简单问题**（"茅台股价"、"今天的新闻"）→ **1 步计划**
2. **复合问题**（"涨停 + 市值最大 + 公告"、"宁德时代为什么跌 + 上下游"）→ **2-4 步计划**
3. 每一步必须有具体的 `target_skill` + `args`（除非是"最后总结"步骤，target_skill 可以是 null）
4. **args 的语义占位（重要）**：当后面步骤要引用前一步的输出时，**必须**用占位符：
   - `<step_0_top_stock>` — 第 0 步结果中按市值最大的那只股票的「名称 + 代码」组合
   - `<step_0_top_name>` — 只取名称
   - `<step_0_top_code>` — 只取代码
   - `<step_0_first>` — 第 0 步的第一行（JSON 字符串）
   示例（查"涨停 + 市值最大"那只的公告）：
   ```
   step 1: target="financial-query", args={"query": "今日A股涨停股票,按总市值降序排序", "limit": 5}
   step 2: target="announcement-search", args={"query": "<step_0_top_stock> 最近公告", "days": 30, "limit": 10}
   ```
   **绝对不要**自己脑补具体标的（如 "admin"、"unknown"、"待定"）— 那样会查不到东西
5. **从问题里**提取关键限制（时间、范围、数量）— 用户给的时间窗口必须带进 args
6. **不要** plan 一个"最后总结"步骤 — synthesizer 会自动整合
7. **不要**重复同一步的 args — 如果前一步已发起的 query 拿到 0 结果，你应该让用户换个问法（兜底），而不是用相同的 args 再发一次

# 例子
用户：「涨停的股票中市值最大的那只最近的公告和研报」
输出：
{{
  "plan": [
    {{"goal": "找涨停且市值最大的股票", "target_skill": "financial-query",
     "args": {{"query": "今日A股涨停股票,按总市值降序排序", "limit": "5"}}}},
    {{"goal": "查那只股票的近期研报", "target_skill": "report-search",
     "args": {{"query": "<step_0_top_name>的研报", "days": "30", "limit": "10"}}}},
    {{"goal": "查那只股票的最近公告", "target_skill": "announcement-search",
     "args": {{"query": "<step_0_top_name>的公告", "days": "30", "limit": "10"}}}}
  ],
  "rationale": "先取 top 股票，再分别查它的研报和公告"
}}

用户：「茅台股价多少」
输出：
{{"plan": [{{"goal": "取最新价", "target_skill": "financial-query",
            "args": {{"query": "贵州茅台 最新价"}}}}],
         "rationale": "单步问题，一查即得"}}

用户：「今天杭州天气怎么样」
输出：
{{"plan": [{{"goal": "查实时天气", "target_skill": "anysearch",
            "args": {{"action": "search", "query": "杭州 今天天气", "max_results": 5}}}}],
         "rationale": "实时问题，走联网搜索"}}
"""


def _try_parse_plan(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a plan JSON from the LLM output."""
    text = text.strip()
    # Strip markdown code fences if present
    if "```" in text:
        for fence in text.split("```"):
            fence = fence.strip()
            if fence.startswith("json"):
                fence = fence[4:].strip()
            if fence.startswith("{"):
                text = fence
                break
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to find the first {...} block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    plan = obj.get("plan")
    if not isinstance(plan, list):
        return None
    # Validate / coerce each step
    clean: list[dict[str, Any]] = []
    for step in plan:
        if not isinstance(step, dict):
            continue
        clean.append({
            "goal": str(step.get("goal", "")),
            "target_skill": step.get("target_skill"),
            "args": step.get("args", {}) if isinstance(step.get("args"), dict) else {},
        })
    return {"plan": clean, "rationale": obj.get("rationale", "")}


def _requests_both_announcement_and_report(user_query: str) -> bool:
    """True when the user explicitly asks for both announcements and reports."""
    q = user_query or ""
    has_announcement = any(k in q for k in ("公告", "披露", "announcement", "filing"))
    has_report = any(k in q for k in ("研报", "研究报告", "research report"))
    if not (has_announcement and has_report):
        return False
    # "公告或研报" means either source is acceptable; "公告和研报" means both.
    if any(k in q for k in ("或", "或者", "二选一", "任一")):
        return False
    return True


def _normalize_plan_for_query(user_query: str, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Patch common LLM under-planning for "top stock + 公告和研报".

    The planner sometimes emits only announcement-search for a question
    that asks for both announcements and reports. For this high-traffic
    pattern, make the two dependent lookups explicit and deterministic.
    """
    if not plan or not _requests_both_announcement_and_report(user_query):
        return plan

    try:
        financial_idx = next(
            i for i, step in enumerate(plan)
            if step.get("target_skill") == "financial-query"
        )
    except StopIteration:
        return plan

    if financial_idx != 0:
        # Placeholders are indexed by prior tool-call order. Keep this
        # normalization conservative unless the financial query is first.
        return plan

    def _followup_step(skill: str) -> dict[str, Any]:
        if skill == "report-search":
            return {
                "goal": "查询市值最大涨停股的近期研报",
                "target_skill": "report-search",
                "args": {"query": "<step_0_top_name>的研报", "days": "30", "limit": "10"},
            }
        return {
            "goal": "查询市值最大涨停股的近期公告",
            "target_skill": "announcement-search",
            "args": {"query": "<step_0_top_name>的公告", "days": "30", "limit": "10"},
        }

    normalized: list[dict[str, Any]] = []
    inserted = False
    for idx, step in enumerate(plan):
        if step.get("target_skill") in ("report-search", "announcement-search"):
            continue
        normalized.append(step)
        if idx == financial_idx and not inserted:
            normalized.append(_followup_step("report-search"))
            normalized.append(_followup_step("announcement-search"))
            inserted = True

    return normalized


async def planner_node(state: AgentState) -> dict[str, Any]:
    """Decompose the user question into a multi-step plan.

    Re-entry behavior: if the state already has a `plan` (from a prior
    call or from a replan), do nothing. This is what makes replan work
    — the reflector clears the plan to force a re-invocation.
    """
    user_query = state.get("user_query", "")
    history = state.get("history", []) or []
    # If the planner is invoked a second time (replan), prepend the
    # prior plan + all tool results so the LLM has the full context.
    prior_plan = state.get("plan") or []
    prior_calls = state.get("tool_calls") or []

    if prior_plan:
        # Replan: a previous plan exists but was exhausted. Re-decompose.
        logger.info("planner: replanning (had %d-step plan)", len(prior_plan))
    else:
        logger.info("planner: first-time planning for query=%r", user_query[:80])

    settings = get_settings()
    llm = build_chat_model(settings, temperature=0.0)

    history_text = "\n".join(f"[{m['role']}] {m['content']}" for m in history[-6:])

    # Build a compact skill summary so the planner knows what's available.
    skill_lines = []
    for s in REGISTRY.list_specs():
        params = ", ".join(
            f"{p.name}{'' if p.required else '?'}: {p.type}" for p in s.parameters
        )
        skill_lines.append(f"- {s.name}({params}) — {s.description[:120]}")
    skills_text = "\n".join(skill_lines) or "(无可用 Skill)"

    # On replan, include prior steps + their results so the LLM can
    # build a follow-up plan that picks up where we left off.
    prior_text = ""
    if prior_calls:
        prior_text = "\n\n# 已完成的工具调用（按时间顺序）\n" + "\n".join(
            f"### Step {i}: {c.get('name')}({json.dumps(c.get('args', {}), ensure_ascii=False)})\n"
            f"Result summary: {json.dumps((c.get('result') or {}).get('data'), ensure_ascii=False)[:600]}"
            for i, c in enumerate(prior_calls)
        )

    user_prompt = (
        f"# 对话历史（最近 6 条）\n{history_text or '（无）'}\n\n"
        f"# 用户最新问题\n{user_query}\n\n"
        f"# 可用 Skill\n{skills_text}"
        f"{prior_text}\n\n"
        "请按 system prompt 中的契约输出 plan JSON。"
    )

    try:
        resp = await llm.ainvoke(
            [SystemMessage(content=PLANNER_PROMPT), HumanMessage(content=user_prompt)]
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("planner LLM call failed")
        # Fallback: empty plan → router's LLM path will drive the
        # question reactively. Don't emit a fake 1-step plan with
        # null skill; that short-circuits the whole run.
        return {
            "plan": [],
            "pending_step_index": 0,
            "error": f"planner LLM call failed: {exc}",
        }

    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    parsed = _try_parse_plan(content)
    if not parsed or not parsed.get("plan"):
        logger.warning("planner: failed to parse plan (raw output: %r), falling back to reactive router", content[:300])
        return {
            "plan": [],
            "pending_step_index": 0,
        }

    # Validate: every step's target_skill (if not None) must exist + be enabled.
    clean_plan: list[dict[str, Any]] = []
    for step in parsed["plan"]:
        skill = step.get("target_skill")
        if skill is None:
            clean_plan.append(step)
            continue
        if not REGISTRY.get_spec(skill):
            logger.warning("planner: unknown skill %r in plan, dropping step", skill)
            continue
        if not REGISTRY.is_enabled(skill):
            logger.warning("planner: disabled skill %r in plan, dropping step", skill)
            continue
        clean_plan.append(step)

    # Edge case: planner gave us an empty plan after validation —
    # fall back to letting the router LLM handle it reactively.
    if not clean_plan:
        logger.warning("planner: every planned step was invalid, falling back to reactive router")
    else:
        clean_plan = _normalize_plan_for_query(user_query, clean_plan)

    logger.info(
        "planner: produced %d-step plan: %s",
        len(clean_plan),
        [s.get("target_skill") for s in clean_plan],
    )
    return {
        "plan": clean_plan,
        "pending_step_index": 0,
        # Clear any stale hint from a prior reflector turn.
        "next_skill_hint": None,
        "next_args_hint": None,
    }
