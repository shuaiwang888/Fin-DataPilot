"""Synthesizer node: stream the final natural-language answer to the user."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.prompts.system import render_system_prompt
from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model

logger = logging.getLogger(__name__)


SYNTH_PROMPT = """你是 Fin-DataPilot 的总结器。基于以下 Skill 调用结果，用清晰、自然的中文输出最终答案。
- 不要重复粘贴全部原始 JSON；挑选最关键的字段，必要时用 Markdown 表格。
- 涉及数据时不要附加"数据来源"字样，平台会统一处理。
- 如果所有 Skill 都失败了，礼貌说明并建议换个问法。
"""


async def synthesize(state: AgentState) -> AsyncIterator[dict[str, Any]]:
    """Stream token deltas + emit the final message at the end."""
    settings = get_settings()
    llm = build_chat_model(settings, temperature=0.2)

    user_query = state.get("user_query", "")
    calls = state.get("tool_calls", [])
    results_text = "\n\n".join(
        f"### Skill: {c['name']}\nArgs: {json.dumps(c.get('args', {}), ensure_ascii=False)}\n"
        f"OK: {c.get('ok')}  Duration: {c.get('duration_ms')}ms\n"
        f"Result: {json.dumps(c.get('result'), ensure_ascii=False)[:3000]}"
        for c in calls
    )

    user_prompt = (
        f"用户问题：{user_query}\n\n"
        f"已调用的 Skill 结果：\n{results_text or '（无）'}\n\n"
        "请输出最终答案。"
    )

    final_text = ""
    try:
        async for chunk in llm.astream(
            [SystemMessage(content=SYNTH_PROMPT), HumanMessage(content=user_prompt)]
        ):
            delta = chunk.content if hasattr(chunk, "content") else ""
            if isinstance(delta, str) and delta:
                final_text += delta
                yield {"event": "token_delta", "data": {"text": delta}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("synthesizer streaming failed")
        yield {"event": "error", "data": {"message": f"总结失败: {exc}"}}

    if not final_text and calls:
        # Fallback if LLM produced nothing
        last = calls[-1]
        if last.get("ok") and last.get("result"):
            final_text = f"查询完成。以下是 Skill `{last['name']}` 返回的核心数据：\n\n```json\n{json.dumps(last['result'].get('data'), ensure_ascii=False, indent=2)[:2000]}\n```"
        else:
            final_text = "抱歉，未能获取到数据。"

    yield {"event": "message_final", "data": {"content": final_text, "tool_calls": calls}}
