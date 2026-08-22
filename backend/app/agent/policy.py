"""Non-LLM policy gates for financial-agent execution.

These rules are intentionally evaluated in code at both the API entry point
and immediately before tool dispatch. Prompts may explain the policy, but
they are not an enforcement boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


FINANCIAL_ADVICE_TERMS = ("买入", "卖出", "推荐", "建仓", "加仓", "减仓", "止损", "目标价", "值得买", "该买吗")
EXECUTION_TERMS = ("下单", "交易", "转账", "提现吗", "代买", "代卖")
LONG_WINDOW_TERMS = {
    "一年": 365,
    "半年": 180,
    "三个月": 90,
    "2个月": 60,
    "两个月": 60,
    "一个月": 31,
    "1个月": 31,
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None
    notices: tuple[str, ...] = ()


def assess_user_query(query: str) -> PolicyDecision:
    text = query.lower()
    if any(term in text for term in EXECUTION_TERMS):
        return PolicyDecision(
            allowed=False,
            code="EXECUTION_NOT_SUPPORTED",
            message="本系统仅提供研究与信息检索，不执行或代为执行任何交易、转账或账户操作。",
        )
    notices: list[str] = []
    if any(term in text for term in FINANCIAL_ADVICE_TERMS):
        notices.append("内容仅供研究参考，不构成个性化投资建议或交易指令；请结合自身风险承受能力独立判断。")
    return PolicyDecision(allowed=True, notices=tuple(notices))


def assess_tool_call(name: str, args: dict[str, Any]) -> PolicyDecision:
    """Enforce the 30-day financial-query limit on continuous indicator windows."""
    if name != "financial-query":
        return PolicyDecision(allowed=True)
    query = str(args.get("query", ""))
    days = _continuous_window_days(query)
    if days is not None and days > 30:
        return PolicyDecision(
            allowed=False,
            code="FINANCIAL_QUERY_WINDOW_EXCEEDED",
            message=(
                "合规限制：financial-query 不允许查询连续超过 30 天的金融指标时间窗。"
                "请拆分为不超过 30 天的独立区间，或改用公告、研报、新闻等适用数据源。"
            ),
        )
    return PolicyDecision(allowed=True)


def _continuous_window_days(query: str) -> int | None:
    """Return an explicit continuous-window length found in a natural query.

    We only block explicit time windows. Terms such as “2024 年财报” are a
    reporting period, not a day-by-day continuous indicator request.
    """
    normalized = query.replace(" ", "")
    numeric_months = re.search(r"(?:近|过去|连续|最近)?(\d{1,2})个月", normalized)
    if numeric_months:
        return int(numeric_months.group(1)) * 30
    for term, days in LONG_WINDOW_TERMS.items():
        if term in normalized and any(marker in normalized for marker in ("近", "过去", "连续", "日", "每天", "日度", "走势", "行情")):
            return days
    match = re.search(r"(?:近|过去|连续|最近)?(\d{1,4})\s*天", normalized)
    if match:
        return int(match.group(1))
    # ISO-like explicit date range, inclusive. Do not guess other ambiguous
    # natural language date formats.
    match = re.search(r"(20\d{2}-\d{1,2}-\d{1,2})\s*(?:至|到|~|－|-)\s*(20\d{2}-\d{1,2}-\d{1,2})", normalized)
    if match:
        try:
            start = date.fromisoformat(match.group(1))
            end = date.fromisoformat(match.group(2))
            return abs((end - start).days) + 1
        except ValueError:
            return None
    return None
