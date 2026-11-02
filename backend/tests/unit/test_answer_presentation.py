"""Charts must preserve evidence values and avoid ambiguous numeric fields."""
from __future__ import annotations

import math
from typing import Any

import pytest

from app.agent.presentation import build_presentation


def financial(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": "financial-query", "ok": True, "trace_id": "trace-1", "result": {
        "data": {"datas": rows}, "source": "问财", "as_of": "2026-11-02T10:00:00Z",
    }}


def test_charts_preserve_negative_values_units_and_provenance():
    result = build_presentation([financial([
        {"股票简称": "甲", "股票代码": "000001", "涨跌幅": "-2.50%", "成交额": "2,000万"},
        {"股票简称": "乙", "股票代码": "000002", "涨跌幅": "3.00%", "成交额": "3,000万"},
    ])])
    assert result["status"] == "ready"
    assert [chart["title"] for chart in result["charts"]] == ["涨跌幅", "成交额"]
    chart = result["charts"][0]
    assert chart["points"] == [{"label": "甲", "value": -2.5}, {"label": "乙", "value": 3.0}]
    assert chart["unit"] == "%"
    assert chart["source"] == "问财"
    assert chart["trace_id"] == "trace-1"
    assert chart["as_of"] == "2026-11-02T10:00:00Z"


@pytest.mark.parametrize("first,second", [
    ("2亿", "3万"), ("2%", 3), (None, 3), (True, False),
    (math.nan, 3), (math.inf, 3), ("1e309", 3), ("--", 3),
])
def test_ambiguous_or_nonfinite_values_are_not_charted(first, second):
    assert not build_presentation([financial([
        {"名称": "甲", "指标": first}, {"名称": "乙", "指标": second},
    ])])["charts"]


def test_duplicate_labels_and_news_never_produce_numeric_charts():
    call = financial([{"名称": "甲", "营收": 2}, {"名称": "甲", "营收": 3}])
    assert not build_presentation([call])["charts"]
    call["name"] = "news-search"
    assert not build_presentation([call])["charts"]


def test_chart_limits_and_incomplete_evidence_are_explicit():
    rows = [{"名称": f"公司{i}", **{f"指标{j}": i + j for j in range(6)}} for i in range(30)]
    result = build_presentation([financial(rows), {"name": "news-search", "ok": False}], "达到检索上限")
    assert result["status"] == "partial"
    assert result["successful_tools"] == 1
    assert len(result["warnings"]) == 2
    assert len(result["charts"]) == 3
    assert len(result["charts"][0]["points"]) == 12
    assert result["charts"][0]["total_rows"] == 30
    assert build_presentation([])["status"] == "empty"


@pytest.mark.asyncio
async def test_summary_outage_retains_financial_values_and_marks_partial(monkeypatch):
    from app.agent.nodes import synthesizer

    class OfflineModel:
        async def astream(self, _messages):
            raise RuntimeError("provider unavailable")
            yield  # pragma: no cover

    monkeypatch.setattr(synthesizer, "build_chat_model", lambda *_args, **_kwargs: OfflineModel())
    call = financial([{"名称": "甲", "营收": 200}, {"名称": "乙", "营收": 300}])
    events = [event async for event in synthesizer.synthesize({
        "user_query": "比较营收", "tool_calls": [call],
    })]
    final = next(event["data"] for event in events if event["event"] == "message_final")
    assert "200" in final["content"] and "300" in final["content"]
    assert final["presentation"]["status"] == "partial"
    assert "自动总结" in final["presentation"]["warnings"][0]
    assert "provider unavailable" not in str(final)


def test_requested_metric_takes_priority_over_vendor_default_columns():
    call = financial([
        {"名称": "甲", "最新价": 10, "涨跌幅": 1, "成交额": 20, "市盈率ttm": 8.08},
        {"名称": "乙", "最新价": 11, "涨跌幅": 2, "成交额": 30, "市盈率ttm": 8.55},
    ])
    call["args"] = {"query": "比较两家公司最新市盈率"}
    assert [chart["title"] for chart in build_presentation([call])["charts"]] == ["市盈率ttm"]
