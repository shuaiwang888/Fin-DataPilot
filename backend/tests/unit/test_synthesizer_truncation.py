"""Test the synthesizer's smart truncation of skill results."""
from __future__ import annotations

import json

from app.agent.nodes.synthesizer import (
    _truncate_long_text_fields,
    _truncate_result_for_prompt,
)


def _big_text(n: int) -> str:
    return "研报内容。" * (n // 6 + 1)


def test_short_result_passes_through_unchanged():
    """Small payloads should be serialized verbatim — no truncation noise."""
    result = {"data": {"announcements": [{"title": "x", "content": "short"}]}}
    out = _truncate_result_for_prompt(result, max_chars=30_000, max_item_text_chars=600)
    parsed = json.loads(out)
    assert parsed["data"]["announcements"][0]["content"] == "short"


def test_per_item_content_truncated_to_max_item_chars():
    """Each item's long text fields get truncated to max_item_text_chars,
    but title/metadata are preserved untouched."""
    long = _big_text(2000)  # ~2000 chars
    result = {
        "data": {
            "announcements": [
                {"title": "公告1", "date": "2026-01-01", "content": long},
                {"title": "公告2", "date": "2026-01-02", "content": long},
            ]
        }
    }
    out = _truncate_result_for_prompt(result, max_chars=30_000, max_item_text_chars=600)
    parsed = json.loads(out)
    items = parsed["data"]["announcements"]
    assert len(items) == 2, "all items must be preserved when total fits"
    for item in items:
        assert item["title"] in ("公告1", "公告2")  # metadata preserved
        assert len(item["content"]) <= 650  # 600 + the truncation marker
        assert item["content"].endswith("…(已截断)")
        # Raw text should NOT appear in the output
        assert _big_text(2000)[:1500] not in item["content"]


def test_total_char_budget_drops_trailing_items():
    """When the per-item-trimmed result still exceeds the total budget,
    drop trailing items and append a '还有 N 条未显示' marker."""
    long = _big_text(500)
    # 20 items × ~600 chars of content each = ~12k serialized.
    items = [
        {"title": f"ann{i}", "content": long, "date": "2026-01-01"}
        for i in range(20)
    ]
    result = {"data": {"announcements": items}}
    # Force a tight budget so we have to drop items.
    out = _truncate_result_for_prompt(result, max_chars=4_000, max_item_text_chars=300)
    assert "还有" in out, f"expected drop marker, got: {out[-200:]}"
    # The first items must survive (their titles still appear).
    parsed = json.loads(out.rsplit("\n…(还有", 1)[0] if "…(还有" in out else out)
    kept_titles = {x["title"] for x in parsed["data"]["announcements"]}
    assert "ann0" in kept_titles
    # Trailing items should be gone.
    assert "ann19" not in kept_titles


def test_handles_alternative_array_locations():
    """The drop-trailing-items logic must work regardless of which key
    the items live under: announcements, articles, reports, datas, data, rows."""
    long = _big_text(500)
    for key in ("announcements", "articles", "reports", "datas", "rows"):
        result = {"data": {key: [{"title": f"{key}_item", "content": long} for _ in range(15)]}}
        out = _truncate_result_for_prompt(result, max_chars=3_000, max_item_text_chars=200)
        assert "还有" in out, f"key={key}: expected drop marker in: {out[-100:]}"


def test_handles_top_level_data_list():
    """Some skills put the items list directly under data (no nested key)."""
    long = _big_text(500)
    result = {"data": [{"title": f"item{i}", "content": long} for i in range(15)]}
    out = _truncate_result_for_prompt(result, max_chars=3_000, max_item_text_chars=200)
    assert "还有" in out


def test_no_list_fallback_hard_truncates_with_marker():
    """If no items list can be found, fall back to a hard cut with marker."""
    weird = {"foo": "bar", "data": {"weird_key": "x" * 5000}}
    out = _truncate_result_for_prompt(weird, max_chars=1_000, max_item_text_chars=200)
    # Should end with the fallback marker (no drop-by-item)
    assert out.endswith("…(总长 5000 字符，已截断)") or "已截断" in out


def test_non_text_fields_not_truncated():
    """Fields that aren't long free text (URLs, codes, numbers) must
    not be touched even if they're long."""
    result = {
        "data": {
            "articles": [
                {
                    "title": "x",
                    "url": "https://example.com/" + "a" * 800,
                    "secid": "600519.SH",
                }
            ]
        }
    }
    out = _truncate_result_for_prompt(result, max_chars=30_000, max_item_text_chars=200)
    parsed = json.loads(out)
    item = parsed["data"]["articles"][0]
    assert item["url"].startswith("https://example.com/aaa")  # untouched
    assert item["secid"] == "600519.SH"


def test_truncate_long_text_fields_recursive():
    """The helper itself must walk nested dicts and lists."""
    obj = {
        "data": [
            {"content": _big_text(1500), "title": "t1"},
            {"nested": {"content": _big_text(1500), "title": "t2"}},
        ]
    }
    truncated, saved = _truncate_long_text_fields(obj, max_chars=400)
    assert saved > 0
    assert truncated["data"][0]["content"].endswith("…(已截断)")
    assert truncated["data"][1]["nested"]["content"].endswith("…(已截断)")
    # Titles preserved
    assert truncated["data"][0]["title"] == "t1"
    assert truncated["data"][1]["nested"]["title"] == "t2"