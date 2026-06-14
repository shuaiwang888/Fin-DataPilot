"""Adapter for the announcement-search skill (公告/事件检索)."""
from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings
from app.skills.base import Handler, ToolParameter, ToolResult, ToolSpec
from app.skills.registry import REGISTRY
from app.utils.trace import generate_trace_id

logger = logging.getLogger(__name__)

SKILL_LOCAL_NAME = "announcement-search"
SKILL_PLATFORM_NAME = "announcement-search"
SKILL_VERSION = "1.0.0"
DEFAULT_API_URL = "https://openapi.iwencai.com/v1/comprehensive/search"


async def announcement_search_handler(
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

    platform_name = settings.iwencai_skill_id_map.get(
        SKILL_LOCAL_NAME, SKILL_PLATFORM_NAME
    )
    trace_id = generate_trace_id()

    payload = {
        "query": query,
        "limit": int(limit),
        "days": int(days),
        "channels": ["announcement"],
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

    items = body.get("announcements") or body.get("datas") or body.get("results") or []
    return ToolResult(
        tool=SKILL_LOCAL_NAME,
        ok=True,
        data={"announcements": items, "count": len(items)},
        meta={"raw_status": resp.status_code},
        trace_id=trace_id,
    )


ANNOUNCEMENT_SEARCH_SPEC = ToolSpec(
    name=SKILL_LOCAL_NAME,
    display_name="公告搜索",
    description=(
        "公司公告与事件检索。返回业绩预告、增发、股权质押、限售解禁、机构调研、监管函、分红、回购等公告列表。"
    ),
    category="events",
    parameters=[
        ToolParameter(name="query", type="string", description="搜索关键词（公司名/股票代码/事件类型）"),
        ToolParameter(name="limit", type="string", description="返回条数，默认 10", required=False),
        ToolParameter(name="days", type="string", description="时间范围（天），默认 90", required=False),
    ],
    requires=["IWENCAI_API_KEY"],
    examples=[{"query": "贵州茅台 分红派息", "limit": "10"}],
)

REGISTRY.register(ANNOUNCEMENT_SEARCH_SPEC, announcement_search_handler)
