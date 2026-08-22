"""Three-layer memory storage, recall, and anonymous isolation."""
from __future__ import annotations

import pytest

from app.memory.service import _fallback_memories, build_memory_context
from app.storage.repository import (
    clear_memories_for_user_async,
    create_session_async,
    delete_long_term_memory_for_user_async,
    get_session_memory_async,
    list_long_term_memories_async,
    upsert_long_term_memory_async,
    upsert_session_memory_async,
)

pytestmark = pytest.mark.database


@pytest.mark.asyncio
async def test_short_and_long_memory_are_isolated_by_user() -> None:
    first_session = await create_session_async("first", "anon_first")
    second_session = await create_session_async("second", "anon_second")
    await upsert_session_memory_async(first_session, "anon_first", "偏好低风险", 2)
    await upsert_session_memory_async(second_session, "anon_second", "偏好高风险", 2)
    await upsert_long_term_memory_async(
        "anon_first", "preference", "我偏好低风险投资", "risk:low", 5, first_session
    )

    assert (await get_session_memory_async(first_session, "anon_first"))["summary"] == "偏好低风险"
    assert await get_session_memory_async(first_session, "anon_second") is None
    assert len(await list_long_term_memories_async("anon_first")) == 1
    assert await list_long_term_memories_async("anon_second") == []


@pytest.mark.asyncio
async def test_recall_and_user_scoped_delete() -> None:
    session_id = await create_session_async("memory", "anon_owner")
    memory_id = await upsert_long_term_memory_async(
        "anon_owner", "portfolio", "长期关注贵州茅台", "watch:moutai", 4, session_id
    )
    context = await build_memory_context("anon_owner", session_id, "分析贵州茅台")
    assert "长期关注贵州茅台" in context
    assert not await delete_long_term_memory_for_user_async(memory_id, "anon_other")
    assert await delete_long_term_memory_for_user_async(memory_id, "anon_owner")


@pytest.mark.asyncio
async def test_clear_removes_both_persistent_layers() -> None:
    session_id = await create_session_async("clear", "anon_clear")
    await upsert_session_memory_async(session_id, "anon_clear", "摘要", 2)
    await upsert_long_term_memory_async(
        "anon_clear", "goal", "目标是长期投资", "goal:long", 4, session_id
    )
    deleted = await clear_memories_for_user_async("anon_clear")
    assert deleted == {"long_term": 1, "short_term": 1}
    assert await get_session_memory_async(session_id, "anon_clear") is None
    assert await list_long_term_memories_async("anon_clear") == []


def test_explicit_memory_fallback_rejects_secrets() -> None:
    assert _fallback_memories("请记住：我偏好低波动产品。")
    assert _fallback_memories("请记住：我的 API_KEY 是 abc123。") == []
