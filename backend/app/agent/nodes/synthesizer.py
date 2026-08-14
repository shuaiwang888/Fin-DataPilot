"""Synthesize one user-facing answer after the execution trace is complete."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model

logger = logging.getLogger(__name__)

SYNTH_PROMPT = """你是 Fin-DataPilot 的总结器。基于 Skill 返回的证据，用清晰、自然的中文回答用户。

# 规则
- 只能基于已经给出的 evidence 做结论；缺失的部分必须明确说明，不能编造数据。
- 关于“是否值得买”“风险如何”等判断，必须说明证据支持的条件与风险，不能给出无依据的确定性买卖建议。
- 多行数据优先使用 Markdown 表格，只保留关键字段。
- 只输出一份直接面向用户的 Markdown 最终答案。
- 严禁输出 `<think>`、推理过程、工作步骤、工具调用说明或任何 XML 标签。执行过程由平台单独展示。
"""

MAX_RESULT_CHARS = 30_000
MAX_ITEM_TEXT_CHARS = 600

_OPEN_THINK_RE = re.compile(r"<(?:think|memthink|mm:think|mn-think)\s*>", re.IGNORECASE)
_CLOSE_THINK_RE = re.compile(r"</(?:think|memthink|mm:think|mn-think)\s*>", re.IGNORECASE)
_THINK_BLOCK_RE = re.compile(
    r"<(?:think|memthink|mm:think|mn-think)\s*>([\s\S]*?)"
    r"</(?:think|memthink|mm:think|mn-think)\s*>",
    re.IGNORECASE,
)
_UNCLOSED_THINK_RE = re.compile(
    r"<(?:think|memthink|mm:think|mn-think)\s*>([\s\S]*)",
    re.IGNORECASE,
)
_LONG_TEXT_FIELDS = (
    "content",
    "contents",
    "summary",
    "summaries",
    "abstract",
    "description",
    "text",
    "body",
    "article_text",
    "news_content",
    "announcement_content",
    "report_content",
    "detail",
    "details",
)


def _extract_preamble(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build structured query information from the most recent useful result."""
    for call in reversed(calls or []):
        if not call.get("ok"):
            continue
        data = (call.get("result") or {}).get("data") or {}
        if not isinstance(data, dict):
            continue
        rows = (
            data.get("articles")
            or data.get("announcements")
            or data.get("reports")
            or data.get("datas")
            or []
        )
        code_count = data.get("code_count", 0)
        chunks_info = data.get("chunks_info")
        if isinstance(chunks_info, str):
            with suppress(json.JSONDecodeError):
                chunks_info = json.loads(chunks_info)
        if rows or chunks_info or code_count:
            return {
                "skill_name": call.get("name", ""),
                "args": call.get("args", {}),
                "actual_query": (call.get("args") or {}).get("query", ""),
                "code_count": int(code_count) if code_count is not None else 0,
                "returned_count": len(rows) if isinstance(rows, list) else 0,
                "chunks_info": chunks_info,
            }
    return None


def _truncate_long_text_fields(obj: Any, max_chars: int) -> tuple[Any, int]:
    """Trim large prose fields while preserving result metadata and shape."""
    saved = 0
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if (
                isinstance(value, str)
                and len(value) > max_chars
                and any(field in key.lower() for field in _LONG_TEXT_FIELDS)
            ):
                saved += len(value) - max_chars
                out[key] = value[:max_chars] + "…(已截断)"
            else:
                child, child_saved = _truncate_long_text_fields(value, max_chars)
                out[key] = child
                saved += child_saved
        return out, saved
    if isinstance(obj, list):
        out_list = []
        for item in obj:
            child, child_saved = _truncate_long_text_fields(item, max_chars)
            out_list.append(child)
            saved += child_saved
        return out_list, saved
    return obj, 0


def _truncate_result_for_prompt(result: Any, max_chars: int, max_item_text_chars: int) -> str:
    """Serialize evidence with per-item and total-size bounds."""
    if not result:
        return json.dumps(result, ensure_ascii=False)
    truncated, _saved = _truncate_long_text_fields(result, max_item_text_chars)
    serialized = json.dumps(truncated, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized

    keys = ("announcements", "articles", "reports", "datas", "rows", "data", "items")
    path: list[str] | None = None
    if isinstance(truncated, dict):
        data_obj = truncated.get("data")
        if isinstance(data_obj, dict):
            for key in keys:
                if isinstance(data_obj.get(key), list):
                    path = ["data", key]
                    break
        elif isinstance(data_obj, list):
            path = ["data"]
    if path is None:
        return serialized[:max_chars] + f"\n…(总长 {len(serialized)} 字符，已截断)"

    node: Any = truncated
    for key in path[:-1]:
        node = node[key]
    items: list[Any] = node[path[-1]]
    original_count = len(items)
    while items and len(serialized) > max_chars:
        items.pop()
        serialized = json.dumps(truncated, ensure_ascii=False)
    if len(items) < original_count:
        serialized += f"\n…(还有 {original_count - len(items)} 条因字数预算被省略)"
    return serialized


def _strip_think_artifacts(text: str) -> tuple[str, list[str]]:
    """Strip accidental reasoning tags, returning user text plus discarded text."""
    if not text:
        return "", []
    extracted: list[str] = []

    def remember(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if content:
            extracted.append(content)
        return ""

    cleaned = _THINK_BLOCK_RE.sub(remember, text)
    last_close: re.Match[str] | None = None
    for match in _CLOSE_THINK_RE.finditer(cleaned):
        last_close = match
    first_open = _OPEN_THINK_RE.search(cleaned)
    if last_close and (not first_open or last_close.start() < first_open.start()):
        leaked = cleaned[: last_close.start()].strip()
        if leaked:
            extracted.append(leaked)
        cleaned = cleaned[last_close.end() :]
    unclosed = _UNCLOSED_THINK_RE.search(cleaned)
    if unclosed and cleaned.lstrip().lower().startswith(
        ("<think", "<?think", "<memthink", "<mm:think", "<mn-think")
    ):
        leaked = unclosed.group(1).strip()
        if leaked:
            extracted.append(leaked)
        cleaned = cleaned[: unclosed.start()].rstrip()
    cleaned = _OPEN_THINK_RE.sub("", cleaned)
    cleaned = _CLOSE_THINK_RE.sub("", cleaned)
    return cleaned.strip(), extracted


async def synthesize(state: AgentState) -> AsyncIterator[dict[str, Any]]:
    """Publish one sanitized final answer after all tools and reflections end."""
    settings = get_settings()
    llm = build_chat_model(settings, temperature=0.2)
    calls = state.get("tool_calls", [])
    preamble = _extract_preamble(calls)
    if preamble:
        yield {"event": "preamble", "data": preamble}
    yield {"event": "summary_start", "data": {}}

    max_chars = int(os.environ.get("SYNTH_MAX_RESULT_CHARS", MAX_RESULT_CHARS))
    max_item = int(os.environ.get("SYNTH_MAX_ITEM_CHARS", MAX_ITEM_TEXT_CHARS))
    results_text = "\n\n".join(
        f"### Skill: {call['name']}\nArgs: {json.dumps(call.get('args', {}), ensure_ascii=False)}\n"
        f"OK: {call.get('ok')}  Duration: {call.get('duration_ms')}ms\n"
        f"Result: {_truncate_result_for_prompt(call.get('result'), max_chars, max_item)}"
        for call in calls
    )
    user_prompt = (
        f"用户问题：{state.get('user_query', '')}\n\n"
        f"已调用的 Skill 结果：\n{results_text or '（无）'}\n\n"
        f"执行结束说明（如有）：{state.get('final_answer', '') or '（无）'}\n\n"
        "请只输出最终答案正文。"
    )

    raw_text = ""
    try:
        async for chunk in llm.astream(
            [SystemMessage(content=SYNTH_PROMPT), HumanMessage(content=user_prompt)]
        ):
            delta = chunk.content if hasattr(chunk, "content") else ""
            if isinstance(delta, str):
                raw_text += delta
    except Exception as exc:  # noqa: BLE001
        logger.exception("synthesizer streaming failed")
        yield {"event": "error", "data": {"message": f"总结失败: {exc}"}}

    final_text, _discarded_thinking = _strip_think_artifacts(raw_text)
    if not final_text and calls:
        last = calls[-1]
        if last.get("ok") and last.get("result"):
            data = (last["result"] or {}).get("data")
            final_text = f"查询完成。以下是 Skill `{last['name']}` 返回的核心数据：\n\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)[:2000]}\n```"
        else:
            final_text = "抱歉，未能获取到数据。"
    if not final_text:
        final_text = state.get("final_answer") or "抱歉，未能生成最终回答。"

    # The answer bubble is populated only after the whole summary is available.
    yield {"event": "token_delta", "data": {"text": final_text}}
    yield {
        "event": "message_final",
        "data": {"content": final_text, "tool_calls": calls, "preamble": preamble},
    }
