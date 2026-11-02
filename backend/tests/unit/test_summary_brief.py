"""Research answers summarize bounded evidence instead of streaming source dumps."""
from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.answer_evidence import (
    copies_source_prose,
    deduplicate_answer,
    financial_row,
    prepare_answer_evidence,
    render_compact_fallback,
)
from app.agent.followup import looks_like_raw_skill_dump
from app.agent.nodes.synthesizer import _collect_citations, _summary_model_options, synthesize

SOURCE = "https://openapi.iwencai.com/"


def call(name="news-search", rows=None, trace="n1", query="同花顺近期新闻", days="30"):
    rows = rows if rows is not None else [{"title": "同花顺发布新产品", "summary": "新产品面向专业机构。", "url": "https://example.com/news", "publish_date": "2026-10-28"}]
    row_key = {"financial-query": "datas", "news-search": "articles", "announcement-search": "announcements"}.get(name, "reports")
    citations = [{"id": name + ":query", "url": SOURCE, "query": query, "title": name + " 查询结果"}]
    citations += [{"id": name + str(i), "url": row["url"], "title": row["title"]} for i, row in enumerate(rows) if row.get("url")]
    return {"name": name, "ok": True, "trace_id": trace, "args": {"query": query, "days": days}, "result": {
        "data": {row_key: rows}, "as_of": "2026-11-02T08:00:00Z", "source": "问财", "citations": citations,
    }}


def test_projection_deduplicates_articles_bodies_and_leaves_audit_unchanged():
    row = {"uid": "same", "url": "https://example.com/a", "title": "公司新产品", "summary": "事实说明。" * 200, "source_original": "事实说明。" * 200, "publish_date": "2026-10-28"}
    raw = [call(rows=[row, dict(row)])]
    original = copy.deepcopy(raw)
    prepared, _ = prepare_answer_evidence(raw)
    items = prepared[0]["result"]["data"]["items"]
    assert len(items) == 1
    assert len(items[0]["excerpt"]) <= 240
    assert "source_original" not in str(prepared)
    assert raw == original


def test_old_disclosures_are_not_recent_evidence_even_when_reindexed_today():
    row = {"title": "同花顺股东减持公告", "url": "https://example.com/old", "publish_date": "2024-11-30", "index_time": 1793606400, "summary": "旧减持计划"}
    prepared, warnings = prepare_answer_evidence([call("announcement-search", [row], days="90")])
    assert prepared[0]["result"]["data"]["items"] == []
    assert all(citation["url"] != row["url"] for citation in prepared[0]["result"]["citations"])
    assert any("时间窗" in text for text in warnings)
    assert any("不能据此断言" in text for text in warnings)


def test_channel_pages_are_not_independent_company_events():
    row = {"title": "同花顺股票频道—行情与资讯", "url": "https://stock.10jqka.com.cn/", "publish_date": "2026-11-01", "summary": "其他上市公司新闻"}
    prepared, warnings = prepare_answer_evidence([call(rows=[row])])
    assert not prepared[0]["result"]["data"]["items"]
    assert any("聚合页" in text for text in warnings)


def test_earnings_brief_keeps_facts_before_repeated_analyst_writeups():
    rows = [
        {"title": "同花顺三季报点评：AI生态拓展", "summary": "机构预测增长", "url": "https://example.com/opinion1", "publish_date": "2026-10-30"},
        {"title": "同花顺三季报点评：盈利能力提升", "summary": "另一份机构观点", "url": "https://example.com/opinion2", "publish_date": "2026-10-29"},
        {"title": "同花顺发布前三季度业绩", "summary": "归母净利润同比增长", "url": "https://example.com/facts", "publish_date": "2026-10-22"},
    ]
    prepared, _ = prepare_answer_evidence([call(rows=rows)])
    items = prepared[0]["result"]["data"]["items"]
    assert len(items) == 2
    assert items[0]["url"] == "https://example.com/facts"
    assert items[1]["kind"] == "机构观点"


def test_daily_columns_become_a_dated_endpoint_summary_not_latest_day_return():
    row = {"股票简称": "同花顺", "收盘价[20261102]": 110, "收盘价[20261030]": 100, "涨跌幅[20261102]": 2}
    compact = financial_row(row)
    series = next(item for item in compact["series_summary"] if item["metric"] == "收盘价")
    assert series["start"] == "20261030" and series["end"] == "20261102"
    assert series["endpoint_change_pct"] == 10
    assert series["observations"] == 2
    assert row["收盘价[20261102]"] == 110


def test_same_vendor_endpoint_preserves_distinct_tool_queries():
    prepared, _ = prepare_answer_evidence([
        call("financial-query", [{"名称": "甲", "净流入": 100}], trace="f1", query="主力净流入"),
        call("financial-query", [{"名称": "甲", "收盘价": 10}], trace="f2", query="近期收盘价"),
        call("announcement-search", [], trace="a1", query="近期公告"),
    ])
    citations = _collect_citations(prepared)
    assert len(citations) == 3
    assert [citation["trace_id"] for citation in citations] == ["f1", "f2", "a1"]
    assert len({citation["display_id"] for citation in citations}) == 3


def test_valid_markdown_citation_is_not_misclassified_as_a_json_dump():
    assert not looks_like_raw_skill_dump("[S1] 公司资金指标与走势存在分化。")
    assert looks_like_raw_skill_dump('[{"title":"原始资讯"}]')


def test_duplicate_paragraphs_are_removed_without_losing_distinct_facts():
    sentence = "主力资金净流入与特大单净流出方向不一致，不能直接推断机构一致看多。"
    result = deduplicate_answer(sentence + "\n\n走势仍需结合成交量观察。\n\n" + sentence)
    assert result.count(sentence) == 1
    assert "成交量" in result


class FakeModel:
    def __init__(self, streamed="", repaired="结论：资金与走势存在分化。\n\n### 证据边界\n近期公告不足。"):
        self.streamed = streamed
        self.ainvoke = AsyncMock(return_value=SimpleNamespace(content=repaired))
        self.messages = []

    async def astream(self, messages):
        self.messages = messages
        yield SimpleNamespace(content=self.streamed, response_metadata={})


async def test_empty_summary_is_repaired_instead_of_falling_back_to_article_dump():
    model = FakeModel()
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=model):
        events = [event async for event in synthesize({"user_query": "分析同花顺的资金面、消息面和近期走势", "tool_calls": [call()]})]
    final = next(event["data"] for event in events if event["event"] == "message_final")
    model.ainvoke.assert_awaited_once()
    assert final["summary_meta"]["mode"] == "model"
    assert final["summary_meta"]["attempts"] == 2
    assert "围绕「" not in final["content"]
    assert "资金与走势存在分化" in final["content"]


async def test_copied_source_prose_never_reaches_token_stream_before_repair():
    body = "这是用于测试的很长的一段资讯原文，含有公司业务、行业动态与未经归纳的描述。" * 20
    rows = [{"title": "新产品", "summary": body, "url": "https://example.com/a", "publish_date": "2026-11-01"}]
    model = FakeModel(streamed=body)
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=model):
        events = [event async for event in synthesize({"user_query": "分析同花顺", "tool_calls": [call(rows=rows)]})]
    visible = "".join(event["data"]["text"] for event in events if event["event"] == "token_delta")
    assert body[:180] not in visible
    assert "资金与走势存在分化" in visible
    model.ainvoke.assert_awaited_once()


async def test_failed_repair_is_short_and_contains_no_article_bodies():
    rows = [{"uid": str(i), "title": f"事件{i}", "summary": "不应直接显示的资讯原文。" * 200, "url": f"https://example.com/{i}", "publish_date": "2026-11-01"} for i in range(12)]
    model = FakeModel()
    model.ainvoke.side_effect = RuntimeError("upstream unavailable")
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=model):
        events = [event async for event in synthesize({"user_query": "分析同花顺", "tool_calls": [call(rows=rows)]})]
    final = next(event["data"] for event in events if event["event"] == "message_final")
    assert final["summary_meta"]["mode"] == "fallback"
    assert len(final["content"]) < 1400
    assert "不应直接显示的资讯原文" not in final["content"]
    assert "自动总结" in final["content"]
    assert final["presentation"]["status"] == "partial"


async def test_only_used_citations_are_appended_to_the_answer():
    model = FakeModel(streamed="公司已发布新产品，经营影响仍待核实。[S2]")
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=model):
        events = [event async for event in synthesize({"user_query": "分析同花顺", "tool_calls": [call()]})]
    final = next(event["data"] for event in events if event["event"] == "message_final")
    assert [citation["display_id"] for citation in final["citations"]] == ["S2"]
    assert SOURCE not in final["content"]


@pytest.mark.parametrize("provider,model,disabled", [
    ("minimax", "MiniMax-M3", True),
    ("minimax", "MiniMax-M3.1-Flash", False),
    ("openai", "other-model", False),
])
def test_summary_options_only_disable_thinking_for_supported_model(provider, model, disabled):
    options = _summary_model_options(provider, model)
    assert (options.get("extra_body", {}).get("thinking") == {"type": "disabled"}) is disabled


def test_compact_fallback_preserves_values_but_not_full_source_text():
    prepared, warnings = prepare_answer_evidence([
        call("financial-query", [{"名称": "同花顺", "主力资金净流入": 1000, "收盘价[20261030]": 100, "收盘价[20261102]": 110}], query="资金走势"),
        call(),
    ])
    body = render_compact_fallback(prepared, warnings=warnings)
    assert "1,000" in body and "+10.00%" in body
    assert "### 资金面" in body and "### 近期走势" in body and "### 消息面" in body
    assert not copies_source_prose(body, prepared)


async def test_thinking_only_output_is_not_published_and_gets_a_summary_retry():
    model = FakeModel(streamed="<think>private reasoning without a public answer")
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=model):
        events = [event async for event in synthesize({"user_query": "分析同花顺的资金面、消息面和近期走势", "tool_calls": [call()]})]
    visible = "".join(event["data"]["text"] for event in events if event["event"] == "token_delta")
    assert "private reasoning" not in visible
    assert "资金与走势存在分化" in visible
    model.ainvoke.assert_awaited_once()


async def test_repair_with_length_finish_reason_does_not_pass_as_a_complete_summary():
    model = FakeModel()
    model.ainvoke.return_value = SimpleNamespace(content="一段未完整返回的分析", response_metadata={"finish_reason": "length"})
    with patch("app.agent.nodes.synthesizer.build_chat_model", return_value=model):
        events = [event async for event in synthesize({"user_query": "分析同花顺", "tool_calls": [call()]})]
    final = next(event["data"] for event in events if event["event"] == "message_final")
    assert final["summary_meta"]["mode"] == "fallback"
    assert "一段未完整返回" not in final["content"]
