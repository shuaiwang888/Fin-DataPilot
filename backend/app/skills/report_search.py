"""Adapter for the report-search skill (研报全文检索)."""
from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings
from app.skills.base import ToolParameter, ToolResult, ToolSpec
from app.skills.provenance import IWENCAI_SOURCE, item_citations, retrieved_at
from app.skills.registry import REGISTRY
from app.utils.trace import generate_trace_id

logger = logging.getLogger(__name__)

SKILL_LOCAL_NAME = "report-search"
SKILL_PLATFORM_NAME = "report-search"
SKILL_VERSION = "1.0.0"  # must match Skills/report-search/SKILL.md frontmatter
DEFAULT_API_URL = "https://openapi.iwencai.com/v1/comprehensive/search"


async def report_search_handler(
    *,
    query: str,
    limit: str = "10",
    days: str = "90",
) -> ToolResult:
    settings = get_settings()
    api_key = settings.iwencai_api_key
    if not api_key or api_key == "your-iwencai-key-here":
        return ToolResult(
            tool=SKILL_LOCAL_NAME, ok=False, error="IWENCAI_API_KEY is not configured"
        )

    platform_name = settings.iwencai_skill_id_map.get(SKILL_LOCAL_NAME, SKILL_PLATFORM_NAME)
    trace_id = generate_trace_id()

    payload = {
        "query": query,
        "limit": int(limit),
        "days": int(days),
        "channels": ["report"],
        "app_id": "AIME_SKILL",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Claw-Call-Type": "normal",
        "X-Claw-Skill-Id": platform_name,
        "X-Claw-Skill-Version": SKILL_VERSION,
        "X-Claw-Plugin-Id": "none",
        "X-Claw-Plugin-Version": "none",
        "X-Claw-Trace-Id": trace_id,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(DEFAULT_API_URL, json=payload, headers=headers)
        body = resp.json() if resp.content else {}
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        return ToolResult(
            tool=SKILL_LOCAL_NAME, ok=False, error=f"{type(exc).__name__}: {exc}", trace_id=trace_id
        )

    # iWencai /v1/comprehensive/search: array under `data` (not `reports`/`datas`/`results`).
    status_code = body.get("status_code", -1) if isinstance(body, dict) else -1
    if resp.status_code != 200 or status_code != 0:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"iWencai API error (HTTP {resp.status_code}, status {status_code}): {body.get('status_msg', '')}",
            data=body,
            trace_id=trace_id,
        )

    items = body.get("data") or []
    return ToolResult(
        tool=SKILL_LOCAL_NAME,
        ok=True,
        data={
            "reports": items,
            "count": len(items),
            "status_msg": body.get("status_msg", ""),
        },
        meta={"raw_status": resp.status_code, "returned": len(items)},
        trace_id=trace_id,
        source=IWENCAI_SOURCE,
        as_of=(as_of := retrieved_at()),
        citations=item_citations(items, skill=SKILL_LOCAL_NAME, query=query, as_of=as_of),
        raw_ref=f"iwencai://comprehensive/search/{trace_id}",
    )


REPORT_SEARCH_SPEC = ToolSpec(
    name=SKILL_LOCAL_NAME,
    display_name="研报搜索",
    description="研究报告全文检索。返回研报标题、机构、分析师、评级、目标价、发布时间、摘要。",
    category="research",
    parameters=[
        ToolParameter(name="query", type="string", description="搜索关键词（公司/行业/主题）", min_length=1, max_length=500),
        ToolParameter(name="limit", type="string", description="返回条数，默认 10", required=False, default="10", pattern=r"(?:[1-9]|[1-9][0-9]|100)"),
        ToolParameter(name="days", type="string", description="时间范围（天），默认 90", required=False, default="90", pattern=r"(?:[1-9]|[1-9][0-9]|[12][0-9]{2}|365)"),
    ],
    returns_schema={"type": "object", "required": ["reports", "count"], "properties": {"reports": {"type": "array"}, "count": {"type": "integer"}}},
    requires=["IWENCAI_API_KEY"],
    examples=[{"query": "宁德时代", "limit": "10"}],
)

REGISTRY.register(REPORT_SEARCH_SPEC, report_search_handler)
