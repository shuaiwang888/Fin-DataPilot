"""Reflector node: evaluate whether the tool result is sufficient to answer."""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.config import get_settings
from app.llm import build_chat_model
from app.skills.registry import REGISTRY

logger = logging.getLogger(__name__)


REFLECTOR_PROMPT = """你是 Fin-DataPilot 的反思器（Reflector）。判断**已有所有工具调用结果**是否已**完整**回答用户的问题。

# 关键思维
用户的问句**经常有多个子目标**，例如：
- "涨停的股票中，市值最大的那只最近的公告或者研报内容"
  → 子目标 1：找出"涨停 + 市值最大"的股票（financial-query）
  → 子目标 2：那只股票的公告 / 研报（announcement-search / report-search）
- "宁德时代为什么跌 + 跟谁有关联"
  → 子目标 1：行情 / 资金 / 公告
  → 子目标 2：上下游 / 竞品 / 关联股

**判定原则**：
- 把用户问句**拆成所有子目标**，逐个核对是否已有 evidence 覆盖。
- 任意一个子目标没有覆盖 → 判 `need_more`。
- "数据非空" 不等于 "够用" — 一份 50 只股票的列表本身**没有**回答"那只最大的"是谁的公告；必须**显式追问**。
- "数据足够" 不等于 "已精确" — 如果用户问的是单只股票的资料、单个具体数字、单一URL，不允许用列表敷衍。

# 输出严格 JSON（不带 markdown 代码块）
{{
  "verdict": "sufficient" | "need_more" | "failed",
  "reason": "<简短说明判断依据>",
  "next_skill_hint": "<可选，下一步推荐调用的 skill 名>",
  "next_args_hint": {{ ... }}   // 可选，下一步推荐的 args
}}

- **sufficient**：所有子目标已覆盖，可进入 Synthesizer
- **need_more**：还有子目标未覆盖；推荐填 `next_skill_hint` + `next_args_hint` 让 Router 直接接力
- **failed**：明显错误或接口报错，应直接告知用户

# 提示
- `next_skill_hint` 必须是**当前已注册且启用**的 skill（`financial-query` / `news-search` / `announcement-search` / `report-search` / `anysearch`）
- `next_args_hint` 应包含"用前一步结果中的哪个具体标的 / 关键词 / 时间窗口"——例如 `{"query": "<前一步 top-1 的股票名 + 公告>", "days": "30"}`
- 不强求 hint 完美，Router 会基于它再调一次 LLM
"""


async def reflector_node(state: AgentState) -> dict[str, Any]:
    settings = get_settings()
    if not settings.agent_enable_reflection:
        return {"reflection_verdict": "sufficient"}

    calls = state.get("tool_calls", [])
    if not calls:
        return {"reflection_verdict": "sufficient"}

    last = calls[-1]
    user_query = state.get("user_query", "")

    # Quick heuristic: empty/error → failed
    if not last.get("ok"):
        return {
            "reflection_verdict": "failed",
            "reflection": f"工具调用失败: {last.get('error')}",
        }
    result = last.get("result") or {}
    data = result.get("data")
    # A skill may legitimately return a string (e.g. anysearch `extract`
    # returns Markdown, or anysearch `search` returns Markdown when the
    # CLI's output isn't JSON). Coerce to a dict for the row-counting
    # heuristic so we don't crash on `str.get(...)`.
    if isinstance(data, str):
        # Non-empty text IS the answer — short-circuit and skip the LLM
        # reflection round-trip. Empty string = "no results", same as
        # the empty-list case below.
        if data.strip():
            return {
                "reflection_verdict": "sufficient",
                "reflection": f"skill returned {len(data):,} chars of text",
            }
        data = {}
    if not isinstance(data, dict):
        data = {}
    # Prompt-only skills return a SKILL.md body under data.skill_body —
    # not a list of rows. They never need another tool call; the
    # synthesizer can already see this content in the system prompt
    # context, so we short-circuit the empty-data heuristic.
    if data.get("skill_body"):
        return {
            "reflection_verdict": "sufficient",
            "reflection": "prompt-only skill: body is already in context",
        }
    # Heuristic: zero results → need_more (LLM may rewrite the query).
    # If the skill returned free-form text (e.g. anysearch Markdown),
    # any non-empty text counts as "has data" — we don't try to count
    # rows we don't know about.
    rows = data.get("datas") or data.get("articles") or data.get("announcements") or data.get("reports") or []
    if not rows and not data:
        return {
            "reflection_verdict": "need_more",
            "reflection": "工具返回为空数据",
            **_maybe_clear_plan_for_replan("need_more", state),
        }

    # ---- Deterministic multi-step patterns ----
    # These are common "list-of-N + ask-about-one-of-them" questions
    # where the LLM reflector occasionally says "sufficient" because
    # it sees the data is non-empty. Pattern-match BEFORE the LLM so
    # the loop never gets stuck.
    #
    # Pattern A: "take the (top-1 | first | 涨停 | etc) and look up its
    # 公告 / 研报 / 新闻 / 详情". Triggered when:
    #   - last tool was financial-query returning a list
    #   - user query mentions 公告 / 研报 / 新闻 / 详情 / 最新 / 动态
    pattern_a_skill, pattern_a_args, pattern_a_row = _infer_followup_for_list_result(
        last_call_name=last.get("name", ""),
        user_query=user_query,
        rows=rows,
    )
    if pattern_a_skill and pattern_a_args and pattern_a_row is not None:
        return {
            "reflection_verdict": "need_more",
            "reflection": (
                f"已取到 {len(rows)} 条结果，但用户问的是其中"
                f"「{_row_label(pattern_a_row)}」的 {pattern_a_skill} 内容，"
                f"需再调一次该 skill"
            ),
            "next_skill_hint": pattern_a_skill,
            "next_args_hint": pattern_a_args,
            **_maybe_clear_plan_for_replan("need_more", state),
        }

    # Otherwise let the LLM decide — but give it the full multi-call
    # history (not just the latest), so it can recognize the "we got a
    # list of 50 stocks, now we need announcement data for the top one"
    # pattern and emit a next_skill_hint.
    llm = build_chat_model(settings, temperature=0.0)
    history_text = "\n\n".join(
        f"### Skill: {c['name']}({json.dumps(c.get('args', {}), ensure_ascii=False)})\n"
        f"Result: {_truncate(c.get('result'), 1500)}"
        for c in calls
    )
    user_prompt = (
        f"用户问题：{user_query}\n\n"
        f"已调用的 Skill（按时间顺序）：\n{history_text}\n\n"
        "请拆解用户问句的所有子目标，逐个核对是否已有 evidence 覆盖。"
        "如果还有子目标没覆盖，verdict=need_more 并给出 next_skill_hint / next_args_hint。"
    )

    try:
        resp = await llm.ainvoke(
            [SystemMessage(content=REFLECTOR_PROMPT), HumanMessage(content=user_prompt)]
        )
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        text = text.strip()
        if "```" in text:
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        obj = json.loads(text)
        verdict = obj.get("verdict", "sufficient")
        if verdict not in ("sufficient", "need_more", "failed"):
            verdict = "sufficient"
        out: dict[str, Any] = {
            "reflection_verdict": verdict,
            "reflection": obj.get("reason", ""),
            **_maybe_clear_plan_for_replan(verdict, state),
        }
        # Forward the hint to the router — even if the LLM wrote a bad
        # hint, the router validates + falls back to its own LLM call,
        # so this is a pure win when right and a no-op when wrong.
        hint_skill = obj.get("next_skill_hint")
        hint_args = obj.get("next_args_hint")
        if hint_skill and isinstance(hint_skill, str) and REGISTRY.get_spec(hint_skill):
            out["next_skill_hint"] = hint_skill
            if isinstance(hint_args, dict):
                out["next_args_hint"] = hint_args
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("reflector LLM call failed (%s) — defaulting to sufficient", exc)
        return {"reflection_verdict": "sufficient"}


# ---- Plan exhaustion → trigger re-plan --------------------------------


def _maybe_clear_plan_for_replan(verdict: str, state: AgentState) -> dict[str, Any]:
    """If the verdict is need_more but the plan is exhausted, clear the
    plan so the next router iteration re-enters via the planner
    instead of falling through to its own LLM call.

    This is how a multi-step "react to unexpected data" re-plan kicks
    in. The planner LLM call gets the full tool history and re-emits
    a new plan.
    """
    if verdict != "need_more":
        return {}
    plan = state.get("plan") or []
    pending_idx = state.get("pending_step_index", 0)
    if plan and pending_idx < len(plan):
        # Plan still has steps → router will advance naturally. No
        # re-plan needed.
        return {}
    # Plan is exhausted (or never existed). Clear it so the next
    # planner invocation runs with the latest context.
    if plan:
        logger.info("reflector: plan exhausted + need_more → clearing plan for re-plan")
    return {"plan": [], "pending_step_index": 0}


def _truncate(result: Any, max_chars: int) -> str:
    """Compact-stringify a tool result for inclusion in the reflector prompt."""
    try:
        s = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(result)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"\n…(已截断，原 {len(s):,} chars)"


# ---- Deterministic multi-step helpers ----------------------------------


# Keywords that signal "user wants detail on a specific entity from the
# list" — Chinese + English variants seen in real Fin-DataPilot traffic.
_FOLLOWUP_KEYWORDS = (
    # announcements
    "公告", "公告内容", "公告全文", "近期公告", "最新公告", "披露",
    # research
    "研报", "研报内容", "研报观点", "研究报告", "研报全文", "深度研报",
    "券商研报", "机构研报", "近期研报", "最新研报",
    # news
    "新闻", "最新新闻", "近期新闻", "新闻内容", "资讯", "快讯",
    # generic "details"
    "详情", "详细介绍", "基本信息", "公司概况", "资料", "动态",
    "基本面", "财务", "经营", "业绩", "财报", "季报", "年报",
    "近况", "近 30 天", "近30天", "近 7 天", "近7天", "近一年", "近 1 年",
    "深度", "解读", "研判", "深度分析", "行业地位", "护城河",
    "股东", "机构持仓", "前十大股东", "大股东",
    # English (in case LLM feeds English content)
    "announcement", "research", "report", "news", "details", "overview",
    "latest", "recent", "details", "filings",
)


def _row_label(row: dict[str, Any]) -> str:
    """Pretty-print a financial-query row for reflection text."""
    name = row.get("股票简称") or row.get("name") or row.get("简称") or "未知"
    code = row.get("股票代码") or row.get("code") or row.get("代码") or ""
    if code:
        return f"{name}({code})"
    return name


def _pick_top_row(rows: list[dict[str, Any]], user_query: str) -> dict[str, Any] | None:
    """Pick the row the user is most likely asking about.

    Heuristics, in order:
      1. "市值最大/最高" → row with max 总市值 (or 市场总值 / market_cap)
      2. "涨幅最大/最高" → row with max 涨跌幅
      3. "最小/最低" → row with min
      4. Otherwise → rows[0] (iWencai already sorts by relevance / default)
    """
    if not rows:
        return None
    q = (user_query or "").lower()

    def _num(row: dict, *keys: str) -> float | None:
        for k in keys:
            v = row.get(k)
            if v is None:
                continue
            try:
                return float(str(v).replace(",", "").replace("%", ""))
            except (TypeError, ValueError):
                continue
        return None

    if any(k in user_query for k in ("市值最大", "市值最高", "市值第一", "最大市值")):
        ranked = sorted(
            rows,
            key=lambda r: _num(r, "总市值", "A股市值", "总市值(亿元)", "market_cap") or 0.0,
            reverse=True,
        )
        return ranked[0]
    if any(k in user_query for k in ("市值最小", "市值最低", "最小市值")):
        ranked = sorted(
            rows,
            key=lambda r: _num(r, "总市值", "A股市值", "总市值(亿元)", "market_cap") or 0.0,
        )
        return ranked[0]
    if any(k in user_query for k in ("涨幅最大", "涨幅最高", "涨幅第一", "涨停", "涨最多")):
        ranked = sorted(
            rows,
            key=lambda r: _num(r, "涨跌幅", "涨幅", "最新涨跌幅", "change_pct") or 0.0,
            reverse=True,
        )
        return ranked[0]
    if any(k in user_query for k in ("跌幅最大", "跌幅最深", "跌最多")):
        ranked = sorted(
            rows,
            key=lambda r: _num(r, "涨跌幅", "涨幅", "最新涨跌幅", "change_pct") or 0.0,
        )
        return ranked[0]
    # Default: iWencai usually pre-sorts by relevance / score, so [0]
    # is the most relevant.
    return rows[0]


def _infer_followup_for_list_result(
    *,
    last_call_name: str,
    user_query: str,
    rows: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Return (skill, args, row) for the deterministic follow-up step,
    or (None, None, None) if the pattern doesn't match.

    Pattern: "list-of-N + ask-about-X's detail"
    The follow-up skill is chosen by the keyword the user uses:
      公告 / 披露 / 财务 / 业绩 / 财报 / 季报 / 股东 → announcement-search
      研报 / 深度 / 解读 / 券商 → report-search
      新闻 / 资讯 / 动态 / 近况 → news-search
      else → announcement-search (most common "next step" after
        financial-query)
    """
    if not rows or not isinstance(rows, list):
        return (None, None, None)
    # Only fire when the previous tool produced a list of stocks/entities
    # (financial-query, news-search, etc). We don't want to chain
    # search → search, or extract → search.
    if last_call_name not in ("financial-query", "news-search", "announcement-search", "report-search"):
        return (None, None, None)

    q = user_query or ""
    if not any(kw in q for kw in _FOLLOWUP_KEYWORDS):
        return (None, None, None)

    target_row = _pick_top_row(rows, q)
    if not target_row:
        return (None, None, None)

    name = target_row.get("股票简称") or target_row.get("name") or target_row.get("简称") or ""
    code = target_row.get("股票代码") or target_row.get("code") or target_row.get("代码") or ""
    if not name and not code:
        return (None, None, None)
    query_term = " ".join(filter(None, [name, code]))

    # Decide which skill to call next. Priority: 公告 > 研报 > 新闻
    # because for "X 的公告或研报" the user typically wants 公告 first
    # (more immediate, more concrete). We check announcement FIRST so
    # that "公告或研报" routes to announcement-search.
    if any(kw in q for kw in ("公告", "披露", "财报", "季报", "年报", "业绩", "股东",
                              "基本面", "财务", "经营", "公司概况", "资料", "详情",
                              "announcement", "filing", "earnings", "financials")):
        skill = "announcement-search"
        args: dict[str, Any] = {"query": query_term, "limit": "10", "days": "30"}
    elif any(kw in q for kw in ("研报", "深度", "解读", "券商", "研究报告", "research", "report")):
        skill = "report-search"
        args = {"query": query_term, "limit": "10", "days": "30"}
    elif any(kw in q for kw in ("新闻", "资讯", "动态", "近况", "news", "latest")):
        skill = "news-search"
        args = {"query": query_term, "limit": "10", "days": "30"}
    else:
        # Fallback when the user said e.g. "看看它的最新情况" — pick
        # announcement (broadest of the three).
        skill = "announcement-search"
        args = {"query": query_term, "limit": "10", "days": "30"}

    # Make sure the target skill is actually registered (e.g. user
    # disabled it in the UI) — bail if not, so the LLM gets a clean
    # chance to handle it.
    if not REGISTRY.get_spec(skill):
        return (None, None, None)

    return (skill, args, target_row)
