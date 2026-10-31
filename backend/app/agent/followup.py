"""Decide when a new turn should reuse evidence already gathered in the session."""

from __future__ import annotations

import re
from typing import Any

_REWRITE_MARKERS = (
    "可读",
    "易读",
    "好读",
    "读起来",
    "整理成",
    "重新整理",
    "重新组织",
    "换一种",
    "换个说法",
    "写清楚",
    "写得清楚",
    "更清楚",
    "总结一下",
    "总结上面",
    "总结上述",
    "基于上面",
    "基于刚才",
    "改写",
    "重写",
    "排版",
    "分点",
    "通俗",
    "不要表格",
    "别用表格",
    "用文字",
)

_ANAPHORA = ("上面", "刚才", "上一条", "之前的", "这段", "这个回答", "上述", "已有内容")

_FRESH_EVIDENCE_MARKERS = (
    "新闻",
    "公告",
    "研报",
    "股价",
    "行情",
    "市值",
    "资金",
    "财报",
    "为什么",
    "影响",
    "对比",
    "筛选",
    "最近",
)


def is_presentation_followup(user_query: str) -> bool:
    """Whether the user is asking to reshape an answer already in this session.

    A fresh research request such as “整理江淮汽车最近的重要新闻” stays on the
    retrieval path. “把上面的内容整理成可读的回答” does not start a new lookup.
    """
    query = re.sub(r"\s+", "", user_query or "")
    if not query:
        return False
    reshapes = any(marker in query for marker in _REWRITE_MARKERS)
    refers_back = any(marker in query for marker in _ANAPHORA)
    if not reshapes and not (refers_back and any(word in query for word in ("整理", "总结", "改写", "重写"))):
        return False
    asks_for_new_evidence = any(marker in query for marker in _FRESH_EVIDENCE_MARKERS)
    keeps_existing_answer = refers_back or "整理成" in query or "可读" in query
    return not asks_for_new_evidence or keeps_existing_answer


def latest_assistant_evidence(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Return the newest saved skill results and the newest assistant prose."""
    calls: list[dict[str, Any]] = []
    prose = ""
    for message in reversed(history or []):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        if content and not prose:
            prose = content
        raw_calls = message.get("tool_calls") or []
        if raw_calls and not calls:
            calls = [call for call in raw_calls if isinstance(call, dict)]
        if prose and calls:
            break
    return calls, prose


def prepare_followup(user_query: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Seed a turn that should answer from the previous run's evidence."""
    if not is_presentation_followup(user_query):
        return {"reuse_prior_evidence": False, "tool_calls": []}
    calls, prose = latest_assistant_evidence(history)
    if not calls and not prose:
        return {"reuse_prior_evidence": False, "tool_calls": []}
    return {"reuse_prior_evidence": True, "tool_calls": calls}


def render_dialogue(history: list[dict[str, Any]], *, limit: int = 6, per_message: int = 1500) -> str:
    """Render recent turns without pasting a raw skill payload back into a prompt."""
    lines: list[str] = []
    for message in (history or [])[-limit:]:
        content = str(message.get("content") or "").strip()
        if looks_like_raw_skill_dump(content):
            content = "（此轮曾直接展示技能原始数据。请改写成可读简报，不要复制字段或 JSON。）"
        elif len(content) > per_message:
            content = content[:per_message] + "…（已截断）"
        role = message.get("role") or "unknown"
        lines.append(f"[{role}] {content or '（空）'}")
    return "\n".join(lines)


def looks_like_raw_skill_dump(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if "以下是 Skill" in stripped and "```" in stripped:
        return True
    if stripped.startswith(("```json", "```JSON", "{", "[")):
        return True
    quotes = stripped.count('"')
    braces = stripped.count("{") + stripped.count("[")
    if braces >= 3 and quotes >= 8:
        chinese = len(re.findall(r"[\u4e00-\u9fff]", stripped))
        if chinese < 40 or chinese * 4 < quotes:
            return True
    return False
