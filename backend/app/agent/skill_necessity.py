"""Deterministic guardrails for deciding whether another Skill is necessary."""

from __future__ import annotations

_EXPLICIT_SKILL_MARKERS: dict[str, tuple[str, ...]] = {
    "announcement-search": (
        "公告", "披露", "财报", "季报", "年报", "业绩预告",
        "announcement", "filing",
    ),
    "news-search": (
        "新闻", "资讯", "快讯", "舆情", "动态", "近况", "news",
    ),
    "report-search": (
        "研报", "研究报告", "券商观点", "机构观点", "research report",
    ),
}

_CROSS_SOURCE_ANALYSIS_MARKERS = (
    "为什么", "原因", "怎么回事", "影响", "利好", "利空", "风险",
    "消息面", "催化", "异动", "分析", "解读", "研判", "投资价值",
    "值得买", "能买吗", "能不能买", "是否买", "该不该买", "下周一买",
    "买入", "卖出", "推荐哪些", "推荐哪",
)

_DIRECT_FINANCIAL_DATA_MARKERS = (
    "股票", "股价", "行情", "市值", "涨停", "跌停", "涨幅", "跌幅",
    "成交", "换手", "资金流", "基金", "指数", "可转债", "期货",
    "营收", "净利润", "财务指标", "交易日", "开盘",
)


def has_cross_source_analysis_intent(user_query: str) -> bool:
    """Whether the user asked for analysis that can justify source expansion.

    Bare movement words such as ``涨`` and ``跌`` are intentionally excluded:
    they also occur in simple data requests like ``列举全部涨停股票``.
    """
    q = (user_query or "").lower()
    return any(marker.lower() in q for marker in _CROSS_SOURCE_ANALYSIS_MARKERS)


def explicitly_requests_skill(user_query: str, skill: str) -> bool:
    """Return True only when the latest question names that evidence family."""
    q = (user_query or "").lower()
    return any(marker.lower() in q for marker in _EXPLICIT_SKILL_MARKERS.get(skill, ()))


def is_skill_necessary_for_query(user_query: str, skill: str) -> bool:
    """Validate that a planned/reflected Skill serves the latest question.

    Structured financial queries remain generally valid. Public web search is
    retained as an empty-result fallback. Vertical financial searches require
    either an explicit request for that source or a genuine analysis/decision
    intent; merely discovering a stock name never authorizes extra lookups.
    """
    if skill in {"financial-query", "anysearch"}:
        return True
    if skill in _EXPLICIT_SKILL_MARKERS:
        return explicitly_requests_skill(user_query, skill) or has_cross_source_analysis_intent(
            user_query
        )
    return True


def is_direct_financial_data_query(user_query: str) -> bool:
    """Whether one or more structured financial lookups can answer the query."""
    q = (user_query or "").lower()
    if not any(marker.lower() in q for marker in _DIRECT_FINANCIAL_DATA_MARKERS):
        return False
    if has_cross_source_analysis_intent(user_query):
        return False
    return not any(explicitly_requests_skill(user_query, skill) for skill in _EXPLICIT_SKILL_MARKERS)
