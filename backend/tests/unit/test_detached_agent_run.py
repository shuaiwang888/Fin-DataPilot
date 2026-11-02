"""Regression tests for Agent execution detached from the SSE subscriber."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.api import agent


async def test_background_run_persists_final_answer_without_sse_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_agent_stream(**_kwargs):
        yield {
            "event": "tool_result",
            "data": {"name": "financial_data", "trace_id": "trace-1", "ok": True},
        }
        yield {"event": "message_final", "data": {"content": "后台完成的答案"}}
        yield {"event": "done", "data": {}}

    append_event = AsyncMock()
    save_message = AsyncMock()
    finish_run = AsyncMock()
    update_memory = AsyncMock()
    unregister = AsyncMock()
    monkeypatch.setattr(agent, "run_agent_stream", fake_agent_stream)
    monkeypatch.setattr(agent, "append_agent_run_event_async", append_event)
    monkeypatch.setattr(agent, "save_message_async", save_message)
    monkeypatch.setattr(agent, "finish_agent_run_async", finish_run)
    monkeypatch.setattr(agent, "update_memory_after_turn", update_memory)
    monkeypatch.setattr(agent.ACTIVE_RUNS, "unregister", unregister)

    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
    await agent._execute_run_background(
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        query="测试问题",
        history=[],
        memory_context="",
        event_queue=queue,
    )

    queued: list[dict[str, object]] = []
    while (item := await queue.get()) is not None:
        queued.append(item)

    assert [item["event"] for item in queued] == [
        "tool_result",
        "message_final",
        "done",
        "run_status",
    ]
    assert queued[-1]["data"] == {
        "run_id": "run-1",
        "status": "completed",
        "error": None,
    }
    assert append_event.await_count == 3
    save_message.assert_awaited_once_with(
        session_id="session-1",
        role="assistant",
        content="后台完成的答案",
        tool_calls=[{"name": "financial_data", "trace_id": "trace-1", "ok": True}],
        thinking={
            "trace": [{"name": "financial_data", "trace_id": "trace-1", "ok": True}],
            "events": [],
            "status": "completed"
        },
    )
    finish_run.assert_awaited_once_with("run-1", "completed", "后台完成的答案", None)
    update_memory.assert_awaited_once()
    unregister.assert_awaited_once_with("run-1")


async def test_cancelled_background_run_reaches_durable_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def slow_agent_stream(**_kwargs):
        started.set()
        await asyncio.Future()
        yield  # pragma: no cover - makes this an async generator

    finish_run = AsyncMock()
    unregister = AsyncMock()
    monkeypatch.setattr(agent, "save_message_async", AsyncMock())
    monkeypatch.setattr(agent, "run_agent_stream", slow_agent_stream)
    monkeypatch.setattr(agent, "finish_agent_run_async", finish_run)
    monkeypatch.setattr(agent.ACTIVE_RUNS, "unregister", unregister)

    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
    task = asyncio.create_task(
        agent._execute_run_background(
            run_id="run-cancelled",
            session_id="session-1",
            user_id="user-1",
            query="测试取消",
            history=[],
            memory_context="",
            event_queue=queue,
        )
    )
    await started.wait()
    task.cancel()
    await task

    status_event = await queue.get()
    assert status_event == {
        "event": "run_status",
        "data": {
            "run_id": "run-cancelled",
            "status": "cancelled",
            "error": "run cancelled",
        },
    }
    assert await queue.get() is None
    finish_run.assert_awaited_once_with(
        "run-cancelled", "cancelled", "本次研究已停止。", "run cancelled"
    )
    unregister.assert_awaited_once_with("run-cancelled")


async def test_tokens_are_transient_and_final_presentation_is_durable(monkeypatch):
    presentation = {"version": 1, "charts": [{"id": "c1"}]}
    calls = [{"name": "financial-query", "args": {"query": "营收"}, "ok": True}]

    async def stream(**_kwargs):
        yield {"event": "phase", "data": {"phase": "researching"}}
        yield {"event": "think_chunk", "data": {"text": "private reasoning"}}
        for _ in range(100):
            yield {"event": "token_delta", "data": {"text": "字"}}
        yield {"event": "message_final", "data": {
            "content": "完整结论", "tool_calls": calls, "presentation": presentation,
        }}

    append = AsyncMock()
    save = AsyncMock()
    monkeypatch.setattr(agent, "run_agent_stream", stream)
    monkeypatch.setattr(agent, "append_agent_run_event_async", append)
    monkeypatch.setattr(agent, "save_message_async", save)
    monkeypatch.setattr(agent, "finish_agent_run_async", AsyncMock())
    monkeypatch.setattr(agent, "update_memory_after_turn", AsyncMock())
    queue = asyncio.Queue()
    attached = asyncio.Event()  # Detached: events must not accumulate in the subscriber queue.
    await agent._execute_run_background(
        run_id="r1", session_id="s1", user_id="u1", query="营收", history=[],
        memory_context="", event_queue=queue, subscriber_attached=attached,
    )
    assert append.await_count == 2
    assert queue.qsize() == 2  # Only the terminal marker and sentinel.
    saved = save.await_args.kwargs
    assert saved["tool_calls"] == calls
    assert saved["thinking"]["presentation"] == presentation
    assert "private reasoning" not in str(saved)


async def test_missing_final_answer_is_failed_not_completed(monkeypatch):
    async def stream(**_kwargs):
        yield {"event": "error", "data": {"message": "无法执行此请求"}}
        yield {"event": "done", "data": {}}

    finish = AsyncMock()
    monkeypatch.setattr(agent, "run_agent_stream", stream)
    monkeypatch.setattr(agent, "append_agent_run_event_async", AsyncMock())
    monkeypatch.setattr(agent, "finish_agent_run_async", finish)
    monkeypatch.setattr(agent, "save_message_async", AsyncMock())
    await agent._execute_run_background(
        run_id="r2", session_id="s1", user_id="u1", query="测试", history=[],
        memory_context="", event_queue=asyncio.Queue(),
    )
    finish.assert_awaited_once_with("r2", "failed", "本次研究未完成，请稍后重试。", "无法执行此请求")
