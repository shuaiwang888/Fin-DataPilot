"""Build bounded, source-backed answer visuals without asking an LLM for numbers."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChartPoint(BaseModel):
    label: str
    value: float


class AnswerChart(BaseModel):
    id: str
    type: Literal["bar"] = "bar"
    title: str
    unit: str = ""
    points: list[ChartPoint]
    source: str
    trace_id: str
    as_of: str | None = None
    total_rows: int


class AnswerPresentation(BaseModel):
    version: Literal[1] = 1
    status: Literal["ready", "partial", "empty"]
    successful_tools: int
    total_tools: int
    warnings: list[str] = Field(default_factory=list)
    charts: list[AnswerChart] = Field(default_factory=list)


_IDENTIFIER = re.compile(r"代码|编码|序号|排名|日期|时间|code|(^|_)id$|rank|date|time", re.I)
_LABEL = re.compile(r"简称|名称|名字|日期|name|label|date", re.I)
_NUMBER = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(%|亿|万|千|元|股|倍)?$")


def _number(value: Any) -> tuple[float, str] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
        return (number, "") if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    match = _NUMBER.fullmatch(value.strip().replace(",", "").replace("，", "").replace("％", "%"))
    if not match:
        return None
    number = float(match[1])
    return (number, match[2] or "") if math.isfinite(number) else None


def _charts(call: Mapping[str, Any], limit: int) -> list[AnswerChart]:
    # Only structured financial records are comparable; news text is never
    # mined for numbers, and identifiers are never treated as measurements.
    if call.get("name") != "financial-query" or not call.get("ok"):
        return []
    result = call.get("result")
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    raw_rows = data.get("datas", data.get("rows")) if isinstance(data, dict) else data
    if not isinstance(raw_rows, list):
        return []
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if len(rows) < 2:
        return []
    labels = [key for key in rows[0] if _LABEL.search(key)]
    if not labels:
        return []
    label_key = labels[0]
    selected = rows[:12]
    # Ambiguous labels make the visual misleading. Keep the evidence table.
    names = [str(row.get(label_key) or "").strip() for row in selected]
    if not all(names) or len(set(names)) != len(names):
        return []
    charts: list[AnswerChart] = []
    for key in rows[0]:
        if key == label_key or _IDENTIFIER.search(key):
            continue
        values = [_number(row.get(key)) for row in selected]
        if any(value is None for value in values):
            continue
        valid = [value for value in values if value is not None]
        units = {value[1] for value in valid}
        if len(units) != 1:
            continue
        unit = valid[0][1]
        charts.append(AnswerChart(
            id=f"{call.get('trace_id', 'chart')}:{key}",
            title=key,
            unit=unit,
            points=[ChartPoint(label=name, value=value[0]) for name, value in zip(names, valid, strict=True)],
            source=str(result.get("source") or "问财金融数据"),
            trace_id=str(call.get("trace_id") or ""),
            as_of=result.get("as_of"),
            total_rows=len(rows),
        ))
        if len(charts) >= limit:
            break
    return charts


def build_presentation(
    calls: Sequence[Mapping[str, Any]], end_note: str = "",
) -> dict[str, Any]:
    """Use only successful tool payloads; cap the output at three charts."""
    successful = sum(call.get("ok") is True for call in calls)
    warnings = [
        f"{call.get('name', '数据查询')} 未成功，相关结论缺少该项证据。"
        for call in calls if not call.get("ok")
    ]
    if end_note:
        warnings.append(end_note)
    charts: list[AnswerChart] = []
    for call in calls:
        if len(charts) >= 3:
            break
        charts.extend(_charts(call, 3 - len(charts)))
    status: Literal["ready", "partial", "empty"] = "ready"
    if not successful:
        status = "empty"
    elif warnings:
        status = "partial"
    return AnswerPresentation(
        status=status,
        successful_tools=successful,
        total_tools=len(calls),
        warnings=list(dict.fromkeys(warnings))[:8],
        charts=charts,
    ).model_dump()
