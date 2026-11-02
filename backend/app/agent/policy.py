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
# Match requests to perform an action, rather than financial vocabulary.
# In particular, “交易日/交易量/交易策略” are ordinary research concepts.
_ACTION = r"(?:下单|挂单|撤单|转账|提现|代买|代卖|买入|卖出|建仓|加仓|减仓|清仓|申购|赎回|交易)"
_MODIFIER = r"(?:请|帮我|替我|代我|为我|给我|立即|立刻|马上|现在|直接|自动|实际|执行|进行|发起|提交|确认|启动|取消|操作|一键|一下|一笔|一次|这笔|此笔|\s){0,8}"
_EXECUTION_PATTERNS = (
    re.compile(r"(?:帮我|帮忙|替我|代我|为我|给我|请你|让你|麻烦你|请|立即|立刻|马上|现在|直接|自动|实际|执行|进行|发起|提交|确认|启动|取消)" + _MODIFIER + _ACTION),
    re.compile(r"(?:^|[，,。；;！？!?\n]|然后|接着|并且|并)\s*" + _MODIFIER + _ACTION + r"(?=\s*(?:买入|卖出|\d|[一二三四五六七八九十百千万]))"),
    re.compile(r"(?:^|[，,。；;！？!?\n])\s*" + _MODIFIER + r"(?:把|将)[^，,。；;！？!?\n]{1,40}?(?:转给|转到|转入|转出)"),
)
_RESEARCH_SUFFIX = re.compile(
    r"^(?:的)?(?:日|量|额|所|数据|记录|明细|统计|策略|模型|规则|制度|时间|时段|"
    r"成本|费用|手续费|风险|建议|时机|信号|逻辑|理由|流程|步骤|原理|机制|失败|原因|条件|限制|示例|"
    r"前|是什么|是什么意思|怎么|如何|是否|能否|吗|么|呢|后多久到账)"
)
_NEGATED_REQUEST = re.compile(r"(?:不要|不必|无需|不用|别|禁止|不是(?:让你)?)\s*$")
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
    if _requests_execution(text):
        return PolicyDecision(
            allowed=False,
            code="EXECUTION_NOT_SUPPORTED",
            message="我可以帮你查询和分析相关数据，但不能替你实际下单、转账或操作账户。",
        )
    notices: list[str] = []
    if any(term in text for term in FINANCIAL_ADVICE_TERMS):
        notices.append("内容仅供研究参考，不构成个性化投资建议或交易指令；请结合自身风险承受能力独立判断。")
    return PolicyDecision(allowed=True, notices=tuple(notices))


def _requests_execution(text: str) -> bool:
    """Conservatively recognize explicit execution requests in each clause.

    Read-only questions and negated requests are allowed. Negation is local
    to a matched request: “不要解释，直接下单” still contains an action.
    Actual capabilities remain restricted by the read-only tool registry.
    """
    for pattern in _EXECUTION_PATTERNS:
        for match in pattern.finditer(text):
            if _NEGATED_REQUEST.search(text[:match.start()]):
                continue
            if _RESEARCH_SUFFIX.match(text[match.end():].lstrip(" \t\n”\"’'」』")):
                continue
            return True
    return False


def assess_tool_call(name: str, args: dict[str, Any]) -> PolicyDecision:
    """Enforce the 30-day financial-query limit on continuous indicator windows."""
    if name != "financial-query":
        return PolicyDecision(allowed=True)
    query = str(args.get("query", ""))
    if "资金面" in query:
        return PolicyDecision(
            allowed=False,
            code="FINANCIAL_QUERY_FORBIDDEN_TERM",
            message=(
                "financial-query 的输入不得包含“资金面”。"
                "请改用用户明确要求的具体指标，例如主力资金净流入、北向资金或融资融券。"
            ),
        )
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
