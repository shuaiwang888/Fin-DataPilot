"""Synthesizer node: stream the final natural-language answer to the user.

Output protocol:
  - preamble: structured info about the skill call (chunks_info, code_count, ...)
  - summary_start: signals that the LLM stream is about to begin
  - token_delta: each LLM token chunk (the raw text; the frontend parses <think>)
  - think_chunk: optional, when the LLM output spans a <think> block boundary we
    emit a `think_chunk` event with the reasoning text so the frontend can render
    it inside the ThinkingPanel
  - message_final: final structured payload for persistence
  - error: LLM call failed
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model

logger = logging.getLogger(__name__)


SYNTH_PROMPT = """你是 Fin-DataPilot 的总结器。基于以下 Skill 调用结果，用清晰、自然的中文输出最终答案。

# 输出格式
- 第一行：用 `<think>...</think>` 包裹你的**思考过程**（用户看不到，但会被记录到 thinking 面板里）。思考内容应简短（≤ 100 字），说明你打算如何组织答案、关注哪些关键字段。
- 第二行开始：正式的**最终答案**，面向用户。

# 内容要求
- 不要重复粘贴全部原始 JSON；挑选最关键的字段，必要时用 Markdown 表格。
- 涉及数据时不要附加"数据来源"字样，平台会统一处理。
- 询问的条件 / 问句 / 数据量等信息平台会自动显示在答案顶部，**不要**再在答案中重复。
- 如果所有 Skill 都失败了，礼貌说明并建议换个问法。
"""


def _extract_preamble(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a structured preamble from the most recent successful tool call.

    Includes the actual `args.query` we sent, the parsed `chunks_info` returned
    by the upstream iWencai gateway, and the total / returned row counts.
    """
    for c in reversed(calls or []):
        if not c.get("ok"):
            continue
        result = c.get("result") or {}
        data = result.get("data") or {}
        if not isinstance(data, dict):
            continue
        rows = data.get("datas") or []
        code_count = data.get("code_count", 0)
        chunks_info = data.get("chunks_info")
        if isinstance(chunks_info, str):
            try:
                chunks_info = json.loads(chunks_info)
            except json.JSONDecodeError:
                pass
        if not rows and not chunks_info and not code_count:
            continue
        return {
            "skill_name": c.get("name", ""),
            "args": c.get("args", {}),
            "actual_query": (c.get("args") or {}).get("query", ""),
            "code_count": int(code_count) if code_count is not None else 0,
            "returned_count": len(rows) if isinstance(rows, list) else 0,
            "chunks_info": chunks_info,
        }
    return None


async def synthesize(state: AgentState) -> AsyncIterator[dict[str, Any]]:
    """Stream the final natural-language answer to the user."""
    settings = get_settings()
    llm = build_chat_model(settings, temperature=0.2)

    user_query = state.get("user_query", "")
    calls = state.get("tool_calls", [])
    preamble = _extract_preamble(calls)

    if preamble:
        yield {"event": "preamble", "data": preamble}

    yield {"event": "summary_start", "data": {}}

    results_text = "\n\n".join(
        f"### Skill: {c['name']}\nArgs: {json.dumps(c.get('args', {}), ensure_ascii=False)}\n"
        f"OK: {c.get('ok')}  Duration: {c.get('duration_ms')}ms\n"
        f"Result: {json.dumps(c.get('result'), ensure_ascii=False)[:3000]}"
        for c in calls
    )

    user_prompt = (
        f"用户问题：{user_query}\n\n"
        f"已调用的 Skill 结果：\n{results_text or '（无）'}\n\n"
        "请按 system prompt 中的格式输出：`<think>...</think>` + 最终答案。"
    )

    final_text = ""
    think_text = ""
    in_think = False
    pending = ""  # carry-over for partial tag matches

    try:
        async for chunk in llm.astream(
            [SystemMessage(content=SYNTH_PROMPT), HumanMessage(content=user_prompt)]
        ):
            delta = chunk.content if hasattr(chunk, "content") else ""
            if not isinstance(delta, str) or not delta:
                continue
            pending += delta

            # Drain pending, splitting on <think> / </think> boundaries.
            # We may have a partial tag at the end (kept for the next chunk).
            while pending:
                if in_think:
                    end_idx = pending.find("</think>")
                    if end_idx == -1:
                        # Keep the last 7 chars in case </think> is split across chunks
                        if len(pending) > 8:
                            emit = pending[:-8]
                            think_text += emit
                            yield {"event": "think_chunk", "data": {"text": emit}}
                            pending = pending[-8:]
                        break
                    emit = pending[:end_idx]
                    if emit:
                        think_text += emit
                        yield {"event": "think_chunk", "data": {"text": emit}}
                    pending = pending[end_idx + len("</think>"):]
                    # Emit a single consolidated think block
                    yield {
                        "event": "think_done",
                        "data": {"text": think_text.strip()},
                    }
                    think_text = ""
                    in_think = False
                else:
                    start_idx = pending.find("<think>")
                    if start_idx == -1:
                        # Keep the last 7 chars in case <think> is split across chunks
                        if len(pending) > 7:
                            emit = pending[:-7]
                            final_text += emit
                            yield {"event": "token_delta", "data": {"text": emit}}
                            pending = pending[-7:]
                        break
                    if start_idx > 0:
                        emit = pending[:start_idx]
                        final_text += emit
                        yield {"event": "token_delta", "data": {"text": emit}}
                    pending = pending[start_idx + len("<think>"):]
                    in_think = True
                    think_text = ""
        # Flush any remaining pending
        if pending:
            if in_think:
                think_text += pending
                yield {"event": "think_chunk", "data": {"text": pending}}
            else:
                final_text += pending
                yield {"event": "token_delta", "data": {"text": pending}}
            pending = ""
        # If we ended mid-think, emit a final consolidated block
        if in_think and think_text.strip():
            yield {"event": "think_done", "data": {"text": think_text.strip()}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("synthesizer streaming failed")
        yield {"event": "error", "data": {"message": f"总结失败: {exc}"}}

    if not final_text and calls:
        last = calls[-1]
        if last.get("ok") and last.get("result"):
            final_text = f"查询完成。以下是 Skill `{last['name']}` 返回的核心数据：\n\n```json\n{json.dumps(last['result'].get('data'), ensure_ascii=False, indent=2)[:2000]}\n```"
        else:
            final_text = "抱歉，未能获取到数据。"

    yield {
        "event": "message_final",
        "data": {
            "content": final_text,
            "tool_calls": calls,
            "preamble": preamble,
        },
    }
