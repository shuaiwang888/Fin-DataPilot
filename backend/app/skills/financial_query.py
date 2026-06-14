"""Adapter wrapping the financial-query skill (同花顺问财 `query2data`).

The skill is a thin async HTTP wrapper around the iWencai OpenAPI.
It honours the 8 X-Claw-* headers required by the gateway and supports
free-form Chinese natural-language queries.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

import httpx

from app.config import get_settings
from app.skills.base import Handler, ToolParameter, ToolResult, ToolSpec
from app.skills.registry import REGISTRY
from app.utils.trace import generate_trace_id

logger = logging.getLogger(__name__)

SKILL_LOCAL_NAME = "financial-query"
# IMPORTANT: although this skill is a general-purpose financial query, the platform-side
# X-Claw-Skill-Id that the iWencai gateway expects is the registration name of the upstream
# skill we share identity with — `hithink-astock-selector`. Without this exact value the
# gateway returns auth/permission errors.
SKILL_PLATFORM_NAME = "hithink-astock-selector"
SKILL_VERSION = "2.0.0"
DEFAULT_API_URL = "https://openapi.iwencai.com/v1/query2data"


async def financial_query_handler(
    *,
    query: str,
    page: str = "1",
    limit: str = "10",
) -> ToolResult:
    """Call the iWencai query2data API. Returns the raw `datas` payload."""
    settings = get_settings()
    api_key = settings.iwencai_api_key
    if not api_key or api_key == "your-iwencai-key-here":
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error="IWENCAI_API_KEY is not configured. Set it in .env or HF Space secrets.",
        )

    platform_name = settings.iwencai_skill_id_map.get(SKILL_LOCAL_NAME, SKILL_PLATFORM_NAME)
    trace_id = generate_trace_id()

    payload = {
        "query": query,
        "page": str(page),
        "limit": str(limit),
        "is_cache": "1",
        "expand_index": "true",
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
    except httpx.HTTPError as exc:
        return ToolResult(
            tool=SKILL_LOCAL_NAME, ok=False, error=f"HTTP error: {exc}", trace_id=trace_id
        )
    except json.JSONDecodeError as exc:
        return ToolResult(
            tool=SKILL_LOCAL_NAME, ok=False, error=f"Invalid JSON: {exc}", trace_id=trace_id
        )

    status_code = body.get("status_code", 0) if isinstance(body, dict) else 0
    if resp.status_code != 200 or status_code != 0:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"iWencai API error (HTTP {resp.status_code}, status {status_code})",
            data=body,
            trace_id=trace_id,
        )

    return ToolResult(
        tool=SKILL_LOCAL_NAME,
        ok=True,
        data={
            "datas": body.get("datas", []),
            "code_count": body.get("code_count", 0),
            "chunks_info": body.get("chunks_info", {}),
        },
        meta={"status_code": status_code, "returned": len(body.get("datas", []))},
        trace_id=trace_id,
    )


FINANCIAL_QUERY_SPEC = ToolSpec(
    name=SKILL_LOCAL_NAME,
    display_name="金融数据查询",
    description=(
        "金融结构化数据统一查询入口。基于同花顺问财 OpenAPI，用自然语言查询 A 股 / 港股 / 美股 / 基金 / 期货 / "
        "ETF / 板块 / 概念 / 指数 / 宏观经济 等全市场结构化数据；支持单标的数据（行情、估值、财务、事件、资金）"
        "以及排名 / TopN / 筛选 / 选股。"
    ),
    category="data",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="自然语言查询问句（中文），例如：'贵州茅台 PE(TTM)'、'银行 股息率前10'、'今日涨停 行业=科技'。",
        ),
        ToolParameter(
            name="page",
            type="string",
            description="分页页码，默认 1。",
            required=False,
        ),
        ToolParameter(
            name="limit",
            type="string",
            description="每页条数，默认 10，最高 500。",
            required=False,
        ),
    ],
    requires=["IWENCAI_API_KEY"],
    examples=[
        {"query": "贵州茅台 最新价", "limit": "5"},
        {"query": "中证 500 指数 当前点位", "limit": "1"},
        {"query": "银行 股息率前10", "limit": "10"},
    ],
)

REGISTRY.register(FINANCIAL_QUERY_SPEC, financial_query_handler)
