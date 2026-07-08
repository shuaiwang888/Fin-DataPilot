"""Reflector node: evaluate whether the tool result is sufficient to answer."""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model
from app.skills.registry import REGISTRY

logger = logging.getLogger(__name__)


REFLECTOR_PROMPT = """你是 Fin-DataPilot 的反思器（Reflector）。判断**已有所有工具调用结果**是否已**完整**回答用户的问题。

# 关键思维
用户的问句**经常有多个子目标**，例如：
- "涨停的股票中，市值最大的那只最近的公告或者研报内容"
  → 子目标 1：找出"涨停 + 市值最大"的股票（financial-query）
  → 子目标 2：那只股票的公告 / 研报（announcement-search / report-search）
- "宁德时代为什么跌 + 跟谁有关联"
  → 子目标 1：行情 / 资金 / 公告
  → 子目标 2：上下游 / 竞品 / 关联股

**判定原则**：
- 把用户问句**拆成所有子目标**，逐个核对是否已有 evidence 覆盖。
- 任意一个子目标没有覆盖 → 判 `need_more`。
- "数据非空" 不等于 "够用" — 一份 50 只股票的列表本身**没有**回答"那只最大的"是谁的公告；必须**显式追问**。
- "数据足够" 不等于 "已精确" — 如果用户问的是单只股票的资料、单个具体数字、单一URL，不允许用列表敷衍。

# 输出严格 JSON（不带 markdown 代码块）
{{
  "verdict": "sufficient" | "need_more" | "failed",
  "reason": "<简短说明判断依据>",
  "next_skill_hint": "<可选，下一步推荐调用的 skill 名>",
  "next_args_hint": {{ ... }}   // 可选，下一步推荐的 args
}}

- **sufficient**：所有子目标已覆盖，可进入 Synthesizer
- **need_more**：还有子目标未覆盖；推荐填 `next_skill_hint` + `next_args_hint` 让 Router 直接接力
- **failed**：明显错误或接口报错，应直接告知用户

# 提示
- `next_skill_hint` 必须是**当前已注册且启用**的 skill（`financial-query` / `news-search` / `announcement-search` / `report-search` / `anysearch`）
- `next_args_hint` 应包含"用前一步结果中的哪个具体标的 / 关键词 / 时间窗口"——例如 `{"query": "<前一步 top-1 的股票名 + 公告>", "days": "30"}`
- 不强求 hint 完美，Router 会基于它再调一次 LLM
"""


async def reflector_node(state: AgentState) -> dict[str, Any]:
    settings = get_settings()
    if not settings.agent_enable_reflection:
        return {"reflection_verdict": "sufficient"}

    calls = state.get("tool_calls", [])
    if not calls:
        return {"reflection_verdict": "sufficient"}

    last = calls[-1]
    user_query = state.get("user_query", "")

    # Quick heuristic: empty/error → failed
    if not last.get("ok"):
        return {
            "reflection_verdict": "failed",
            "reflection": f"工具调用失败: {last.get('error')}",
        }
    result = last.get("result") or {}
    data = result.get("data")
    # A skill may legitimately return a string (e.g. anysearch `extract`
    # returns Markdown, or anysearch `search` returns Markdown when the
    # CLI's output isn't JSON). Coerce to a dict for the row-counting
    # heuristic so we don't crash on `str.get(...)`.
    if isinstance(data, str):
        # Non-empty text IS the answer — short-circuit and skip the LLM
        # reflection round-trip. Empty string = "no results", same as
        # the empty-list case below.
        if data.strip():
            return {
                "reflection_verdict": "sufficient",
                "reflection": f"skill returned {len(data):,} chars of text",
            }
        data = {}
    if not isinstance(data, dict):
        data = {}
    # Prompt-only skills return a SKILL.md body under data.skill_body —
    # not a list of rows. They never need another tool call; the
    # synthesizer can already see this content in the system prompt
    # context, so we short-circuit the empty-data heuristic.
    if data.get("skill_body"):
        return {
            "reflection_verdict": "sufficient",
            "reflection": "prompt-only skill: body is already in context",
        }
    # Heuristic: zero results → need_more (LLM may rewrite the query).
    # If the skill returned free-form text (e.g. anysearch Markdown),
    # any non-empty text counts as "has data" — we don't try to count
    # rows we don't know about.
    rows = data.get("datas") or data.get("articles") or data.get("announcements") or data.get("reports") or []
    if not rows and not data:
        return {
            "reflection_verdict": "need_more",
            "reflection": "工具返回为空数据",
        }

    # Otherwise let the LLM decide — but give it the full multi-call
    # history (not just the latest), so it can recognize the "we got a
    # list of 50 stocks, now we need announcement data for the top one"
    # pattern and emit a next_skill_hint.
    llm = build_chat_model(settings, temperature=0.0)
    history_text = "\n\n".join(
        f"### Skill: {c['name']}({json.dumps(c.get('args', {}), ensure_ascii=False)})\n"
        f"Result: {_truncate(c.get('result'), 1500)}"
        for c in calls
    )
    user_prompt = (
        f"用户问题：{user_query}\n\n"
        f"已调用的 Skill（按时间顺序）：\n{history_text}\n\n"
        "请拆解用户问句的所有子目标，逐个核对是否已有 evidence 覆盖。"
        "如果还有子目标没覆盖，verdict=need_more 并给出 next_skill_hint / next_args_hint。"
    )

    try:
        resp = await llm.ainvoke(
            [SystemMessage(content=REFLECTOR_PROMPT), HumanMessage(content=user_prompt)]
        )
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        text = text.strip()
        if "```" in text:
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        obj = json.loads(text)
        verdict = obj.get("verdict", "sufficient")
        if verdict not in ("sufficient", "need_more", "failed"):
            verdict = "sufficient"
        out: dict[str, Any] = {
            "reflection_verdict": verdict,
            "reflection": obj.get("reason", ""),
        }
        # Forward the hint to the router — even if the LLM wrote a bad
        # hint, the router validates + falls back to its own LLM call,
        # so this is a pure win when right and a no-op when wrong.
        hint_skill = obj.get("next_skill_hint")
        hint_args = obj.get("next_args_hint")
        if hint_skill and isinstance(hint_skill, str) and REGISTRY.get_spec(hint_skill):
            out["next_skill_hint"] = hint_skill
            if isinstance(hint_args, dict):
                out["next_args_hint"] = hint_args
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("reflector LLM call failed (%s) — defaulting to sufficient", exc)
        return {"reflection_verdict": "sufficient"}


def _truncate(result: Any, max_chars: int) -> str:
    """Compact-stringify a tool result for inclusion in the reflector prompt."""
    try:
        s = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(result)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"\n…(已截断，原 {len(s):,} chars)"
