"""Presentation follow-ups reuse saved evidence and never dump skill JSON."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agent.followup import is_presentation_followup, prepare_followup
from app.agent.nodes.planner import planner_node
from app.agent.nodes.skill_router import skill_router_node
from app.agent.nodes.synthesizer import synthesize


def test_fresh_news_request_still_retrieves() -> None:
    query = "整理江淮汽车最近的重要新闻和潜在影响"
    assert is_presentation_followup(query) is False
    assert prepare_followup(query, [])["reuse_prior_evidence"] is False


def test_readability_followup_reuses_saved_skill_results() -> None:
    history = [
        {"role": "user", "content": "整理江淮汽车最近的重要新闻和潜在影响"},
        {
            "role": "assistant",
            "content": '查询完成。以下是 Skill `news-search` 返回的核心数据：\n\n```json\n{"articles": []}\n```',
            "tool_calls": [
                {
                    "name": "news-search",
                    "args": {"query": "江淮汽车 最近重要新闻"},
                    "ok": True,
                    "result": {
                        "data": {
                            "articles": [
                                {
                                    "title": "江淮汽车发布新车计划",
                                    "publish_time": "2026-10-20",
                                    "summary": "公司披露新车型投放节奏。",
                                }
                            ]
                        }
                    },
                }
            ],
        },
    ]

    prepared = prepare_followup("帮我整理成可读性强的回答", history)

    assert prepared["reuse_prior_evidence"] is True
    assert prepared["tool_calls"][0]["name"] == "news-search"


@pytest.mark.asyncio
async def test_planner_does_not_start_a_new_lookup_for_a_rewrite() -> None:
    state = {
        "user_query": "帮我整理成可读性强的回答",
        "reuse_prior_evidence": True,
        "history": [],
        "tool_calls": [{"name": "news-search", "ok": True, "result": {}}],
        "plan": [],
        "pending_step_index": 0,
    }
    with patch("app.agent.nodes.planner.build_chat_model") as build:
        out = await planner_node(state)  # type: ignore[arg-type]

    build.assert_not_called()
    assert out["plan"] == []


@pytest.mark.asyncio
async def test_router_finishes_without_dispatching_when_rewriting() -> None:
    out = await skill_router_node(  # type: ignore[arg-type]
        {
            "user_query": "帮我整理成可读性强的回答",
            "reuse_prior_evidence": True,
            "tool_calls": [{"name": "news-search", "args": {"query": "江淮汽车"}}],
            "plan": [{"target_skill": "news-search", "args": {"query": "江淮汽车"}}],
            "pending_step_index": 0,
        }
    )

    assert out["router_action"] == "finish"
    assert "tool_calls" not in out


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.seen = ""

    async def astream(self, messages):
        self.seen = messages[-1].content
        for chunk in self.chunks:
            yield _Chunk(chunk)


@pytest.mark.asyncio
async def test_synthesizer_rewrites_raw_skill_json_into_a_brief() -> None:
    llm = _FakeLLM(['```json\n{"articles": [{"title": "不应原样出现"}]}\n```'])
    state = {
        "user_query": "帮我整理成可读性强的回答",
        "reuse_prior_evidence": True,
        "history": [
            {"role": "user", "content": "整理江淮汽车最近的重要新闻和潜在影响"},
            {"role": "assistant", "content": '{"articles":[{"title":"原始字段"}]}'},
        ],
        "tool_calls": [
            {
                "name": "news-search",
                "args": {"query": "江淮汽车 最近重要新闻"},
                "ok": True,
                "result": {
                    "data": {
                        "articles": [
                            {
                                "title": "江淮汽车发布新车计划",
                                "publish_time": "2026-10-20",
                                "summary": "公司披露新车型投放节奏。",
                            }
                        ]
                    }
                },
            }
        ],
    }
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=llm):
        events = [event async for event in synthesize(state)]  # type: ignore[arg-type]

    final = next(event for event in events if event["event"] == "message_final")
    content = final["data"]["content"]
    assert "江淮汽车发布新车计划" in content
    assert "```json" not in content
    assert "articles" not in content
    assert "江淮汽车最近的重要新闻" in llm.seen


@pytest.mark.asyncio
async def test_synthesizer_does_not_dump_json_when_the_model_is_empty() -> None:
    llm = _FakeLLM([])
    state = {
        "user_query": "整理江淮汽车最近的重要新闻和潜在影响",
        "tool_calls": [
            {
                "name": "news-search",
                "ok": True,
                "args": {"query": "江淮汽车 新闻"},
                "result": {
                    "data": {
                        "articles": [
                            {"title": "江淮汽车销量回升", "summary": "月度销量同比改善。"}
                        ]
                    }
                },
            }
        ],
    }
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=llm):
        events = [event async for event in synthesize(state)]  # type: ignore[arg-type]

    content = next(event for event in events if event["event"] == "message_final")["data"]["content"]
    assert "江淮汽车销量回升" in content
    assert "核心数据" not in content
    assert "```" not in content
