"""Adapter for the news-search skill (财经资讯全文检索)."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.skills.base import Handler, ToolParameter, ToolResult, ToolSpec
from app.skills.registry import REGISTRY
from app.utils.trace import generate_trace_id

logger = logging.getLogger(__name__)

SKILL_LOCAL_NAME = "news-search"
SKILL_PLATFORM_NAME = "news-search"
SKILL_VERSION = "1.0.0"
DEFAULT_API_URL = "https://openapi.iwencai.com/v1/comprehensive/search"


async def news_search_handler(
    *,
    query: str,
    limit: str = "10",
    days: str = "30",
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
        "channels": ["news"],
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

    # The iWencai /v1/comprehensive/search endpoint returns
    #   { "status_code": 0, "status_msg": "OK", "data": [ {...}, ... ] }
    # i.e. the array lives under `data`, not `articles` / `datas` / `results`.
    # We also pass the whole body through (minus a couple of housekeeping fields)
    # so the synthesizer / frontend can read `status_msg`, `chunks_info`, etc.
    status_code = body.get("status_code", -1) if isinstance(body, dict) else -1
    if resp.status_code != 200 or status_code != 0:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"iWencai API error (HTTP {resp.status_code}, status {status_code}): {body.get('status_msg', '')}",
            data=body,
            trace_id=trace_id,
        )

    articles = body.get("data") or []
    return ToolResult(
        tool=SKILL_LOCAL_NAME,
        ok=True,
        data={
            "articles": articles,
            "count": len(articles),
            "status_msg": body.get("status_msg", ""),
        },
        meta={"raw_status": resp.status_code, "returned": len(articles)},
        trace_id=trace_id,
    )


NEWS_SEARCH_SPEC = ToolSpec(
    name=SKILL_LOCAL_NAME,
    display_name="新闻搜索",
    description="财经资讯全文检索。返回与 query 相关的新闻文章列表（标题、摘要、发布时间、链接）。",
    category="news",
    parameters=[
        ToolParameter(name="query", type="string", description="搜索关键词（中文自然语言）"),
        ToolParameter(name="limit", type="string", description="返回条数，默认 10", required=False),
        ToolParameter(name="days", type="string", description="时间范围（天），默认 30", required=False),
    ],
    requires=["IWENCAI_API_KEY"],
    examples=[{"query": "贵州茅台 最新新闻", "limit": "10"}],
)

REGISTRY.register(NEWS_SEARCH_SPEC, news_search_handler)
