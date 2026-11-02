"""Bound and deduplicate answer evidence; keep full tool payloads in the audit trail."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

ROW_KEYS = ("articles", "announcements", "reports", "datas", "rows", "items", "results")
_SKILL_LABELS = {"news-search": "新闻检索", "announcement-search": "公告检索", "report-search": "研报检索", "anysearch": "公开网页检索"}
TITLE_KEYS = ("title", "news_title", "公告标题", "标题", "stock_name", "股票简称", "证券简称", "name")
BODY_KEYS = ("summary", "abstract", "digest", "snippet", "description", "content", "正文", "source_original")
DATE_KEYS = ("publish_date", "published_at", "publish_time", "pub_time", "date", "发布时间", "公告日期")
_DATED_FIELD = re.compile(r"^(.+?)\[(20\d{6})\]$")
_DATE = re.compile(r"(20\d{2})[-/年]?(\d{2})[-/月]?(\d{2})")


def rows_from_data(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ROW_KEYS:
            if isinstance(data.get(key), list):
                return rows_from_data(data[key])
        return rows_from_data(data.get("data"))
    return []


def first_text(row: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    return ""


def parse_date(value: Any) -> date | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10**12 else value, timezone.utc).date()
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        match = _DATE.search(value)
        if match:
            try:
                return date(*map(int, match.groups()))
            except ValueError:
                pass
    return None


def publication_date(row: Mapping[str, Any]) -> date | None:
    for key in DATE_KEYS:
        parsed = parse_date(row.get(key))
        if parsed:
            return parsed
    url = str(row.get("url") or row.get("link") or "")
    # A cninfo finalpage path names its actual disclosure date. Do not infer
    # dates from other publishers' paths (some contain a placeholder 1970).
    if "cninfo.com.cn/finalpage/" in url:
        return parse_date(url.split("/finalpage/", 1)[1])
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def financial_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce wide daily columns to endpoint/range facts with explicit dates."""
    snapshot: dict[str, Any] = {}
    series: dict[str, list[tuple[str, float]]] = {}
    for key, value in row.items():
        match = _DATED_FIELD.fullmatch(key)
        number = _number(value)
        if match and number is not None:
            series.setdefault(match[1], []).append((match[2], number))
        elif len(snapshot) < 16:
            snapshot[key] = value
    summaries: list[dict[str, Any]] = []
    for metric, points in series.items():
        points.sort()
        first, last = points[0], points[-1]
        summary: dict[str, Any] = {
            "metric": metric, "start": first[0], "end": last[0],
            "first": first[1], "last": last[1], "observations": len(points),
            "minimum": min(point[1] for point in points),
            "maximum": max(point[1] for point in points),
        }
        if len(points) > 1 and first[1] and any(term in metric for term in ("收盘价", "余额")):
            summary["endpoint_change_pct"] = round((last[1] / first[1] - 1) * 100, 4)
        summaries.append(summary)
    return {"snapshot": snapshot, "series_summary": summaries[:10]} if summaries else dict(row)


def _is_aggregate(row: Mapping[str, Any]) -> bool:
    title = first_text(row, TITLE_KEYS)
    url = str(row.get("url") or row.get("link") or "")
    parsed = urlsplit(url)
    return (
        "股票频道" in title
        or ("basic.10jqka.com.cn" in parsed.netloc and parsed.path.endswith(("event.html", "worth.html")))
    )


def prepare_answer_evidence(
    calls: Sequence[Mapping[str, Any]], *, max_items: int = 4, excerpt_chars: int = 240,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Project source rows once, eliminating duplicate bodies and stale disclosures."""
    prepared: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_calls: set[str] = set()
    seen_items: set[str] = set()
    for call in calls:
        name = str(call.get("name") or "数据查询")
        label = _SKILL_LABELS.get(name, name)
        args = call.get("args") or {}
        result = call.get("result")
        if not isinstance(result, dict):
            prepared.append(dict(call))
            continue
        rows = rows_from_data(result.get("data"))
        key = json.dumps([name, args, result.get("data")], sort_keys=True, ensure_ascii=False, default=str)
        if key in seen_calls:
            continue
        seen_calls.add(key)
        if name == "financial-query":
            data: Any = {"datas": [financial_row(row) for row in rows[:8]], "returned_count": len(rows)}
            citations = result.get("citations") or []
        else:
            as_of = parse_date(result.get("as_of")) or datetime.now(timezone.utc).date()
            try:
                days = int(args.get("days", 0))
            except (ValueError, TypeError):
                days = 0
            cutoff = as_of - timedelta(days=days) if days > 0 else None
            candidates: list[dict[str, Any]] = []
            local_seen: set[str] = set()
            stale = 0
            aggregates = 0
            for row in rows:
                when = publication_date(row)
                if cutoff and when and (when < cutoff or when > as_of):
                    stale += 1
                    continue
                if _is_aggregate(row):
                    aggregates += 1
                    continue
                title = first_text(row, TITLE_KEYS)
                url = str(row.get("url") or row.get("link") or "")
                identity = str(row.get("uid") or url or re.sub(r"\W", "", title))
                if identity and (identity in seen_items or identity in local_seen):
                    continue
                if identity:
                    local_seen.add(identity)
                excerpt = first_text(row, BODY_KEYS)
                # Never send summary AND source_original versions of the same article.
                if excerpt.startswith(title) and title:
                    excerpt = excerpt[len(title):].lstrip(" ：:")
                opinion = name == "report-search" or (name != "announcement-search" and (
                    any(term in title for term in ("点评", "研报", "评级", "盈利预测"))
                    or "vReport_Show" in url
                    or bool(re.search(r"投资评级|目标价|维持.{0,8}(?:推荐|买入)|预计.{0,40}归母净利润", excerpt))
                ))
                kind = "机构观点" if opinion else "来源记录"
                candidates.append({
                    "_identity": identity,
                    "title": title, "published_at": when.isoformat() if when else None,
                    "url": url, "excerpt": excerpt[:excerpt_chars], "kind": kind,
                })
            candidates.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
            # Company facts take priority over repeated broker write-ups of
            # the same earnings event. Keep at most one factual story and one
            # attributed opinion per reporting period in a news brief.
            candidates.sort(key=lambda row: row["kind"] == "机构观点")
            selected: list[dict[str, Any]] = []
            topics: set[tuple[str, str]] = set()
            for candidate in candidates:
                text = candidate["title"] + candidate["excerpt"]
                season = next((label for pattern, label in (
                    (r"前三季度|三季报|第三季度|3Q|3q", "三季度"),
                    (r"半年报|上半年|中报", "半年"),
                    (r"年度报告|年报", "年度"),
                ) if re.search(pattern, text)), "")
                topic = (season, candidate["kind"])
                if name == "news-search" and season and topic in topics:
                    continue
                topics.add(topic)
                selected.append(candidate)
                if len(selected) >= max_items:
                    break
            for item in selected:
                seen_items.add(item.pop("_identity"))
            data = {"items": selected, "returned_count": len(rows), "unique_current_count": len(candidates)}
            if not rows and isinstance(result.get("data"), str):
                data = {"excerpt": result["data"][:1600], "unstructured": True}
            elif not rows and isinstance(result.get("data"), dict):
                text = first_text(result["data"], (*BODY_KEYS, "answer", "text", "markdown", "skill_body"))
                if text:
                    data = {"excerpt": text[:1600], "unstructured": True}
            if stale:
                warning = f"{label}返回的 {stale} 条记录不在本次查询时间窗内，已从近期事件证据中排除。"
                warnings.append(warning)
            if aggregates:
                warnings.append(f"{label}的 {aggregates} 条频道/聚合页已排除，不作为独立近期事件。")
            if rows and not selected:
                warnings.append(f"{label}暂无可用于本次时间范围的有效独立记录，不能据此断言近期没有事件。")
            urls = {row["url"] for row in selected if row.get("url")}
            citations = [citation for citation in result.get("citations", []) if citation.get("query") or citation.get("url") in urls]
        prepared.append({**call, "result": {
            "data": data, "source": result.get("source"), "as_of": result.get("as_of"),
            "citations": citations,
        }})
    return prepared, list(dict.fromkeys(warnings))


def _display_value(value: Any) -> str:
    number = _number(value)
    return f"{number:,.4f}".rstrip("0").rstrip(".") if number is not None else str(value)[:60]


def _display_metric(label: str) -> str:
    return re.sub(r"(20\d{2})(\d{2})(\d{2})", r"\1-\2-\3", label).replace("[", "（").replace("]", "）")


def render_compact_fallback(
    calls: Sequence[Mapping[str, Any]], citations: Sequence[Mapping[str, Any]] = (),
    warnings: Sequence[str] = (),
) -> str:
    """Return a short factual brief on failure, never concatenate article bodies."""
    funds: list[str] = []
    trend: list[str] = []
    news: list[str] = []
    generic: list[str] = []
    seen: set[str] = set()

    def ref(call: Mapping[str, Any], url: str = "") -> str:
        for citation in citations:
            if url and citation.get("url") != url:
                continue
            if not url and (citation.get("trace_id") != call.get("trace_id") or not citation.get("query")):
                continue
            return f" [{citation['display_id']}]" if citation.get("display_id") else ""
        return ""

    def append_unique(lines: list[str], text: str) -> None:
        if text not in seen:
            lines.append(text)
            seen.add(text)

    for call in calls:
        if not call.get("ok"):
            continue
        result = call.get("result") or {}
        data = result.get("data") or {}
        if call.get("name") == "financial-query":
            for row in rows_from_data(data):
                snapshot = row.get("snapshot", row)
                for key, value in snapshot.items():
                    if any(term in key for term in ("资金流", "净流入", "净买入", "主力增仓", "特大单")):
                        append_unique(funds, f"{_display_metric(key)}：{_display_value(value)}（按接口原口径）。{ref(call)}")
                for series in row.get("series_summary", []):
                    metric = series["metric"]
                    change = series.get("endpoint_change_pct")
                    if change is None:
                        continue
                    line = (
                        f"{metric}从 {_display_metric(series['start'])} 的 {_display_value(series['first'])}，"
                        f"变为 {_display_metric(series['end'])} 的 {_display_value(series['last'])}，"
                        f"区间变化 {change:+.2f}%。{ref(call)}"
                    )
                    append_unique(trend if "收盘价" in metric else funds, line)
                if not funds and not trend:
                    cells = [f"{_display_metric(key)} {_display_value(value)}" for key, value in snapshot.items()][:5]
                    append_unique(generic, "；".join(cells) + ref(call))
        else:
            for row in rows_from_data(data):
                title = first_text(row, TITLE_KEYS)
                if not title:
                    continue
                when = row.get("published_at")
                append_unique(news, (f"{when}：" if when else "时间未核实：") + title + ref(call, str(row.get("url") or "")))
    if not any((funds, trend, news, generic)):
        return "本次没有取得足够的有效证据，暂时无法形成可靠结论。请明确标的和时间范围后重试。"
    parts = ["**结论：**本次自动总结暂未完成，以下只保留已核实的关键事实；现有材料不足以替代完整的综合判断。"]
    for heading, lines, count in (("资金面", funds, 4), ("近期走势", trend, 3), ("消息面", news, 4), ("关键数据", generic, 4)):
        if lines:
            parts.append(f"### {heading}\n" + "\n".join(f"- {line}" for line in lines[:count]))
    if warnings:
        parts.append("### 证据边界\n" + "\n".join(f"- {warning}" for warning in warnings[:3]))
    return "\n\n".join(parts)


def deduplicate_answer(text: str) -> str:
    """Remove repeated paragraphs while preserving distinct facts and table rows."""
    seen: set[str] = set()
    parts: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        key = re.sub(r"\s+|\[S\d+\]", "", paragraph)
        if len(key) > 20 and key in seen:
            continue
        seen.add(key)
        parts.append(paragraph)
    return "\n\n".join(parts)


def copies_source_prose(answer: str, calls: Sequence[Mapping[str, Any]]) -> bool:
    """Reject long verbatim passages; short factual phrases remain usable."""
    normalized = re.sub(r"\W", "", answer)
    for call in calls:
        if call.get("name") == "financial-query":
            continue
        result = call.get("result") or {}
        for row in rows_from_data(result.get("data")):
            body = first_text(row, (*BODY_KEYS, "excerpt"))
            fragment = re.sub(r"\W", "", body)
            if len(fragment) >= 160 and fragment[:160] in normalized:
                return True
    return False
