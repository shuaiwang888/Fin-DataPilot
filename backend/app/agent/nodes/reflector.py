"""Reflector node: evaluate whether the tool result is sufficient to answer."""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model

logger = logging.getLogger(__name__)


REFLECTOR_PROMPT = """你是 Fin-DataPilot 的反思器（Reflector）。你的任务是判断**最近一次工具调用的结果**是否足够回答用户的问题。

输出严格 JSON（不带 markdown 代码块）：
{{"verdict": "sufficient" | "need_more" | "failed", "reason": "<简述>"}}

- sufficient：数据已足够，可以直接进入 Synthesizer 生成最终答案
- need_more：数据不够，需要再调一次别的 skill 或换参数重试
- failed：数据明显错误或接口报错，应该直接告知用户失败
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

    # Otherwise let the LLM decide
    llm = build_chat_model(settings, temperature=0.0)
    user_prompt = (
        f"用户问题：{user_query}\n\n"
        f"最近一次工具调用：{last['name']}({last.get('args', {})})\n"
        f"结果摘要：{json.dumps(data, ensure_ascii=False)[:2000]}\n\n"
        "请判断：以上结果足够回答用户问题吗？"
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
        return {"reflection_verdict": verdict, "reflection": obj.get("reason", "")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("reflector LLM call failed (%s) — defaulting to sufficient", exc)
        return {"reflection_verdict": "sufficient"}
