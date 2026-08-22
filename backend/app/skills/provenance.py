"""Source provenance helpers shared by external-data skills.

The returned records are deliberately factual: a retrieval timestamp is not
presented as the market's effective timestamp, and a vendor endpoint is not
pretended to be an article URL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

IWENCAI_SOURCE = "同花顺问财 OpenAPI"


def retrieved_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def iwencai_citation(*, skill: str, query: str, as_of: str) -> dict[str, Any]:
    return {
        "id": f"{skill}:iwencai",
        "source": IWENCAI_SOURCE,
        "title": f"{skill} 查询结果",
        "url": "https://openapi.iwencai.com/",
        "query": query,
        "retrieved_at": as_of,
        "note": "该时间为接口查询时点；具体行情/财务数据时点以返回字段为准。",
    }


def item_citations(items: list[Any], *, skill: str, query: str, as_of: str) -> list[dict[str, Any]]:
    """Produce vendor plus up to 20 source-item references when URLs exist."""
    citations = [iwencai_citation(skill=skill, query=query, as_of=as_of)]
    for index, item in enumerate(items[:20], 1):
        if not isinstance(item, dict):
            continue
        url = next((item.get(k) for k in ("url", "link", "href", "article_url") if item.get(k)), None)
        title = next((item.get(k) for k in ("title", "标题", "name", "名称") if item.get(k)), None)
        published_at = next((item.get(k) for k in ("published_at", "publish_time", "发布时间", "date") if item.get(k)), None)
        if url:
            citations.append(
                {
                    "id": f"{skill}:item:{index}",
                    "source": IWENCAI_SOURCE,
                    "title": str(title or f"{skill} 结果 {index}"),
                    "url": str(url),
                    "published_at": str(published_at) if published_at else None,
                    "retrieved_at": as_of,
                }
            )
    return citations
