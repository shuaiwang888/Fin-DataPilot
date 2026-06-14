"""Synthesizer node: stream the final natural-language answer to the user.

Output protocol:
  - preamble: structured info about the skill call (chunks_info, code_count, ...)
  - summary_start: signals that the LLM stream is about to begin
  - think_chunk: reasoning text (inside <think>...</think>)
  - think_done: signals the end of a <think> block
  - token_delta: user-facing answer tokens
  - heartbeat: periodic tick during long LLM thinking (every ~5s) so the
    frontend can render a "💭 思考中…" indicator instead of looking dead
  - message_final: final structured payload
  - error: LLM call failed
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model

logger = logging.getLogger(__name__)


SYNTH_PROMPT = """你是 Fin-DataPilot 的总结器。基于以下 Skill 调用结果，用清晰、自然的中文输出最终答案。

# 严格输出格式（请务必遵守，否则 UI 会错乱）

**你的回复结构**：
```
<think>
[极简：1-2 句话说明你打算怎么组织答案即可，不要再分析数据]
</think>
[正式的最终答案，直接面向用户，Markdown 格式]
```

# 硬性规则（违反任何一条 UI 都会坏）

1. `<think>` 块**只出现一次**，开头与结尾各一对。
2. `<think>` 内容**≤ 50 字**，且**只写组织方式**，不要再列举/分析数据。
3. **`</think>` 之后严禁再写 `<think>`**，也严禁再补充任何"思考片段"。
4. **`</think>` 之后就是答案正文**。一旦开始写答案，就一气呵成写完，**立即停止**，不要再补任何句子。
5. 不要在答案正文里再嵌套 `<think>` 标签。
6. 不要在答案正文里再写"我需要..."、"让我..."等元叙述。

# 错误示例（**禁止**）
✗ 多个 `<think>` 块
✗ `<think>` 块里又写一遍原始数据
✗ 答案写完后再追加"补充思考"
✗ 答案里嵌入 `<mm:think>##` 之类的畸形标签

# 正确示例
✓
<think>用 Markdown 表格整理关键字段，补充涨跌幅上下文。
</think>贵州茅台（600519.SH）最新价 1291.91 元，当日涨 +1.01%。开盘 1271.18，最高 1295.00，最低 1265.01。

# 内容要求
- 涉及数据时不要附加"数据来源"字样，平台会统一处理。
- 询问的条件 / 问句 / 数据量等信息平台会自动显示在答案顶部，**不要**再在答案中重复。
- 如果所有 Skill 都失败了，礼貌说明并建议换个问法。
"""


def _extract_preamble(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a structured preamble from the most recent successful tool call."""
    for c in reversed(calls or []):
        if not c.get("ok"):
            continue
        result = c.get("result") or {}
        data = result.get("data") or {}
        if not isinstance(data, dict):
            continue
        rows = data.get("articles") or data.get("announcements") or data.get("reports") or data.get("datas") or []
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


# Tunables
MAX_PENDING_TAIL = 12  # chars kept back looking for a partial <think>/</think>
HEARTBEAT_INTERVAL = 4.0  # seconds between heartbeat events during long streaming
# If a <think> block keeps growing past this many characters without
# a closing </think> tag, the LLM likely forgot to close it. Abandon
# the think block — emit everything as the answer and close the panel.
MAX_THINK_BLOCK_CHARS = 200


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
    pending = ""
    last_event_ts = time.monotonic()

    try:
        last_emit_ts = time.monotonic()

        async def emit_pending(to_think: bool) -> None:
            """Move everything currently safe in `pending` into the appropriate
            buffer and yield think_chunk / token_delta events. Updates
            `pending` in place to keep only the tag-handling tail."""
            nonlocal final_text, think_text, in_think, last_emit_ts, pending
            changed = True
            while changed:
                changed = False
                if in_think:
                    end_idx = pending.find("</think>")
                    if end_idx >= 0:
                        emit = pending[:end_idx]
                        if emit:
                            think_text += emit
                            yield {"event": "think_chunk", "data": {"text": emit}}
                        pending = pending[end_idx + len("</think>"):]
                        yield {
                            "event": "think_done",
                            "data": {"text": think_text.strip()},
                        }
                        think_text = ""
                        in_think = False
                        changed = True
                        last_emit_ts = time.monotonic()
                    else:
                        # Watchdog: if a NEW <think> tag is detected after
                        # the answer has already started streaming, the
                        # LLM is misbehaving (it should have stopped
                        # thinking once the answer began). Treat the
                        # pending text as answer content, not thinking.
                        if final_text and pending.lstrip().lower().startswith(
                            ("<think>", "<?think", "<memthink")
                        ):
                            # Drop the tag, route the rest as token_delta
                            tag_end = pending.find(">")
                            if tag_end >= 0:
                                pending = pending[tag_end + 1:]
                            in_think = False
                            changed = True
                            continue
                        # Watchdog: if the think block is wildly long
                        # without a closing tag, abandon.
                        if len(think_text) > MAX_THINK_BLOCK_CHARS:
                            closing_tail = pending[-MAX_PENDING_TAIL:]
                            head = pending[: -MAX_PENDING_TAIL] if len(pending) > MAX_PENDING_TAIL else pending
                            if head:
                                think_text += head
                                yield {"event": "think_chunk", "data": {"text": head}}
                            yield {
                                "event": "think_done",
                                "data": {"text": think_text.strip()},
                            }
                            think_text = ""
                            in_think = False
                            pending = closing_tail
                            changed = True
                            last_emit_ts = time.monotonic()
                        # Keep last 12 chars looking for the closing tag,
                        # but flush anything larger every heartbeat to
                        # avoid starving the UI.
                        elif len(pending) > MAX_PENDING_TAIL and (
                            time.monotonic() - last_emit_ts >= HEARTBEAT_INTERVAL
                        ):
                            emit = pending[:-MAX_PENDING_TAIL]
                            think_text += emit
                            yield {"event": "think_chunk", "data": {"text": emit}}
                            pending = pending[-MAX_PENDING_TAIL:]
                            last_emit_ts = time.monotonic()
                else:
                    start_idx = pending.find("<think>")
                    if start_idx >= 0:
                        if start_idx > 0:
                            emit = pending[:start_idx]
                            final_text += emit
                            yield {"event": "token_delta", "data": {"text": emit}}
                            last_emit_ts = time.monotonic()
                        pending = pending[start_idx + len("<think>"):]
                        in_think = True
                        think_text = ""
                        changed = True
                    else:
                        if len(pending) > MAX_PENDING_TAIL and (
                            time.monotonic() - last_emit_ts >= HEARTBEAT_INTERVAL
                        ):
                            emit = pending[:-MAX_PENDING_TAIL]
                            final_text += emit
                            yield {"event": "token_delta", "data": {"text": emit}}
                            pending = pending[-MAX_PENDING_TAIL:]
                            last_emit_ts = time.monotonic()

        async for chunk in llm.astream(
            [SystemMessage(content=SYNTH_PROMPT), HumanMessage(content=user_prompt)]
        ):
            delta = chunk.content if hasattr(chunk, "content") else ""
            if not isinstance(delta, str) or not delta:
                continue
            pending += delta
            last_event_ts = time.monotonic()
            # Heartbeat + force-flush any pending text every HEARTBEAT_INTERVAL
            # seconds. Critical: even tiny pending chunks (< MAX_PENDING_TAIL)
            # must be flushed, otherwise the user sees "still thinking" with
            # zero text in either panel for chunks that arrive faster than
            # 4s but smaller than 12 chars.
            if time.monotonic() - last_emit_ts >= HEARTBEAT_INTERVAL:
                # Force-flush whatever is in `pending` (regardless of size)
                if pending:
                    if in_think:
                        think_text += pending
                        yield {"event": "think_chunk", "data": {"text": pending}}
                    else:
                        final_text += pending
                        yield {"event": "token_delta", "data": {"text": pending}}
                    pending = ""
                yield {
                    "event": "heartbeat",
                    "data": {
                        "ts": time.time(),
                        "in_think": in_think,
                        "pending_chars": 0,
                    },
                }
                last_emit_ts = time.monotonic()
            async for ev in emit_pending(False):
                yield ev

        # End of stream — flush EVERYTHING that's still in `pending`.
        # No more tag-greed: if `</think>` never came, treat everything
        # since the last <think> as a single think block (so the user
        # at least sees the reasoning, even if it's "raw"). Better to
        # show garbled text than to silently drop it.
        if pending:
            if in_think:
                think_text += pending
                yield {"event": "think_chunk", "data": {"text": pending}}
            else:
                final_text += pending
                yield {"event": "token_delta", "data": {"text": pending}}
            pending = ""
        if in_think and think_text.strip():
            yield {"event": "think_done", "data": {"text": think_text.strip()}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("synthesizer streaming failed")
        yield {"event": "error", "data": {"message": f"总结失败: {exc}"}}

    # Post-process the final answer: extract any <think>...</think> (or
    # malformed variants like </mm:think>) that the LLM accidentally left
    # in the answer body, and move them into the thinking stream. This
    # is a defense-in-depth measure: even if the LLM ignores the
    # <think>...</think> formatting rule, the answer bubble will not be
    # polluted with reasoning.
    extracted_thinks: list[str] = []
    if final_text:
        # Accept several malformed tag variants: <think>, <mm:think>, <think>
        think_re = re.compile(
            r"<(?:think|memthink|think)\s*>([\s\S]*?)<(?:/think|/memthink|/think)\s*>",
            re.IGNORECASE,
        )
        # Also handle unclosed think blocks (LLM forgot to close) by
        # taking everything from <think> to the next blank line + header,
        # OR just stripping any leading <think> to the end of the block.
        unclosed_re = re.compile(
            r"<(?:think|memthink|think)\s*>([\s\S]*)",
            re.IGNORECASE,
        )
        for m in think_re.finditer(final_text):
            content = m.group(1).strip()
            if content:
                extracted_thinks.append(content)
        cleaned = think_re.sub("", final_text)
        # If still starts with <think>, treat the rest as leaked thinking
        m = unclosed_re.search(cleaned)
        if m and cleaned.lstrip().lower().startswith(("<think", "<?think", "<memthink")):
            leaked = m.group(1).strip()
            if leaked:
                extracted_thinks.append(leaked)
            # Drop everything from the <think> tag onward (it's all thinking)
            cleaned = cleaned[: m.start()].rstrip() + "\n\n" + cleaned[m.end() :].lstrip() if m.end() < len(cleaned) else cleaned[: m.start()].rstrip()
        # Strip any leftover standalone <think> / </think> / </mm:think> markers
        cleaned = re.sub(
            r"</?(?:think|memthink|think|/think|/memthink|/think|/mn-think|/?think)\s*>",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        # Also strip Qwen-style </mm:think> markers (sometimes appears in newer models)
        cleaned = re.sub(r"<\/?mn-think\s*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned != final_text.strip() or extracted_thinks:
            final_text = cleaned

    # Emit each extracted think as a think_chunk + think_done so the
    # frontend renders them in the ThinkingPanel.
    for txt in extracted_thinks:
        yield {"event": "think_chunk", "data": {"text": txt}}
        yield {"event": "think_done", "data": {"text": txt.strip()}}

    # If the LLM dumped everything into a think block and the answer
    # body is empty, salvage the think text as the answer. The user
    # would otherwise see a fully-loaded ThinkingPanel and a totally
    # empty answer bubble — a worse outcome than "show the answer
    # even if it reads a bit like reasoning."
    if not final_text.strip() and think_text.strip():
        final_text = think_text.strip()
        think_text = ""

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
