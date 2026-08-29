"""Reflector node: evaluate whether the tool result is sufficient to answer."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.skill_necessity import (
    explicitly_requests_skill,
    is_direct_financial_data_query,
    is_skill_necessary_for_query,
    required_vertical_skills_for_query,
)
from app.agent.state import AgentState, ToolCallRecord
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
- 子目标只能来自用户最新问句；工具结果里出现的新标的不是新任务。用户没有要求公告、新闻、研报时，不得因为“可以补充”而调用这些 Skill。
- "今日涨停的股票"、"列举全部涨停股票"取得非空股票列表后必须判 `sufficient`，不能把“涨停”误解为涨跌原因分析。
- 必须按 Skill 描述核对证据类型：`financial-query` 不能覆盖新闻正文、公司公告或机构研报。用户要求“消息面”时，新闻和公告证据未完成就必须 `need_more`；综合公司分析还要核对研报证据。
- "数据非空" 不等于 "够用" — 一份 50 只股票的列表本身**没有**回答"那只最大的"是谁的公告；必须**显式追问**。
- "数据足够" 不等于 "已精确" — 如果用户问的是单只股票的资料、单个具体数字、单一URL，不允许用列表敷衍。
- 四个金融 Skill 可以互补：行情/财务用 `financial-query`，新闻用 `news-search`，公告用 `announcement-search`，研报用 `report-search`。原因分析、风险判断、事件影响、近况解读通常不能只靠一个 Skill。
- `anysearch` 允许用于金融问句的兜底：优先级低于四个金融 Skill；当金融 Skill 返回为空、字段不全、没有回答用户问句，或需要公开网页/实时事实核查时，再推荐 `anysearch`。

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
- 若某个金融 Skill 返回 0 条：优先换一种自然问法重试一次；再不行才用 `anysearch`（`{"action":"search","query":"...", "domain":"finance"}`）兜底。
- 不强求 hint 完美，Router 会基于它再调一次 LLM
"""


async def reflector_node(state: AgentState) -> dict[str, Any]:
    settings = get_settings()
    if not settings.agent_enable_reflection:
        # Disabling LLM reflection must not disable execution of an explicit
        # Planner plan.  Previously this returned ``sufficient`` after the
        # first tool result, so a four-step plan silently stopped at step 1.
        plan = state.get("plan") or []
        pending_idx = state.get("pending_step_index", 0)
        if pending_idx < len(plan):
            return {
                "reflection_verdict": "need_more",
                "reflection": "反思模型已关闭，继续执行 Planner 的剩余步骤",
            }
        return {"reflection_verdict": "sufficient"}

    calls = state.get("tool_calls", [])
    if not calls:
        return {"reflection_verdict": "sufficient"}

    last = calls[-1]
    user_query = state.get("user_query", "")

    # A failed step must not discard independent evidence steps that remain in
    # an explicit plan. Continue the plan and let synthesis report the failed
    # dimension honestly; only fail immediately when no planned work remains.
    if not last.get("ok"):
        plan = state.get("plan") or []
        pending_idx = state.get("pending_step_index", 0)
        if pending_idx < len(plan):
            return {
                "reflection_verdict": "need_more",
                "reflection": (
                    f"本步 {last.get('name') or 'Skill'} 调用失败，"
                    "继续执行不依赖该结果的剩余计划步骤"
                ),
            }
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
    has_unstructured_text = False
    if isinstance(data, str):
        # A non-empty webpage/search excerpt is evidence, not automatically
        # the answer.  Keep it in the full tool history and let the reflector
        # compare it with every sub-goal; this prevents a single search result
        # from prematurely ending a multi-part investigation.
        has_unstructured_text = bool(data.strip())
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
    if has_unstructured_text:
        # A text result can satisfy a simple one-step web lookup, but it must
        # not skip the rest of an explicit research plan.
        plan = state.get("plan") or []
        pending_idx = state.get("pending_step_index", 0)
        if plan and pending_idx < len(plan):
            return {
                "reflection_verdict": "need_more",
                "reflection": "已取得网页文本，但执行计划仍有待核验子目标",
            }
        return {
            "reflection_verdict": "sufficient",
            "reflection": f"skill returned {len(str(result.get('data') or '')):,} chars of text",
        }
    # Heuristic: zero results → need_more (LLM may rewrite the query).
    # If the skill returned free-form text (e.g. anysearch Markdown),
    # any non-empty text counts as "has data" — we don't try to count
    # rows we don't know about. But the "data" check is a guard for
    # the case where the data field is itself a string (anysearch
    # Markdown). When data is a dict with empty row arrays ({"articles":
    # [], "count": 0}), `not data` is False but rows IS empty — and
    # that's the case we need to handle too.
    rows = data.get("datas") or data.get("articles") or data.get("announcements") or data.get("reports") or []
    if not rows and not has_unstructured_text:
        # When the plan still has more steps, do NOT emit a recovery
        # hint. The router should advance to the next plan step
        # (which may be the same skill with a different query, or a
        # different skill that doesn't depend on this result). Only
        # when the plan is exhausted do we apply the recovery (anysearch
        # fallback / retry with original query).
        plan = state.get("plan") or []
        pending_idx = state.get("pending_step_index", 0)
        plan_has_more = bool(plan) and pending_idx < len(plan)
        if plan_has_more:
            return {
                "reflection_verdict": "need_more",
                "reflection": "本步返回为空，但 plan 还有后续步骤，继续推进",
                **_maybe_clear_plan_for_replan("need_more", state),
            }
        recovery_skill, recovery_args, recovery_reason = _infer_empty_result_recovery(
            calls=calls,
            user_query=user_query,
        )
        empty_out: dict[str, Any] = {
            "reflection_verdict": "need_more",
            "reflection": recovery_reason or "工具返回为空数据",
            **_maybe_clear_plan_for_replan("need_more", state),
        }
        if recovery_skill and recovery_args:
            empty_out["next_skill_hint"] = recovery_skill
            empty_out["next_args_hint"] = recovery_args
        return empty_out

    # A direct structured-data request is complete once its requested rows are
    # available and no still-pending *necessary* structured step remains.
    # This deterministic stop prevents the reflector LLM from inventing
    # announcement/news/report sub-goals for a stock that merely appeared in
    # the result set.
    if last.get("name") == "financial-query" and is_direct_financial_data_query(user_query):
        plan = state.get("plan") or []
        pending_idx = state.get("pending_step_index", 0)
        necessary_pending = [
            step
            for step in plan[pending_idx:]
            if is_skill_necessary_for_query(
                user_query,
                str(step.get("target_skill") or ""),
            )
        ]
        if necessary_pending:
            return {
                "reflection_verdict": "need_more",
                "reflection": "已有部分金融数据，继续完成用户明确要求的剩余查询",
            }
        return {
            "reflection_verdict": "sufficient",
            "reflection": "已取得用户要求的金融数据；无需扩展到公告、新闻或研报",
        }

    # Follow the capability-validated plan before asking the reflector LLM.
    # Returning no hint is intentional: the router advances the pending plan
    # index, avoiding duplicate calls caused by a hint that bypasses it.
    plan = state.get("plan") or []
    pending_idx = state.get("pending_step_index", 0)
    necessary_pending = [
        step
        for step in plan[pending_idx:]
        if is_skill_necessary_for_query(user_query, str(step.get("target_skill") or ""))
    ]
    if necessary_pending:
        pending_names = [str(step.get("target_skill") or "") for step in necessary_pending]
        return {
            "reflection_verdict": "need_more",
            "reflection": f"仍需完成用户问题要求的证据类型：{', '.join(pending_names)}",
        }

    required_vertical = set(required_vertical_skills_for_query(user_query))
    completed_vertical = {
        str(call.get("name") or "")
        for call in calls
        if call.get("ok") and _rows_from_call(call)
    }
    if required_vertical and required_vertical.issubset(completed_vertical):
        return {
            "reflection_verdict": "sufficient",
            "reflection": "用户要求的结构化数据与非结构化金融证据均已覆盖",
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
    missing_skill, missing_args, missing_row = _infer_missing_requested_followup(
        calls=calls,
        user_query=user_query,
    )
    if missing_skill and missing_args and missing_row is not None:
        return {
            "reflection_verdict": "need_more",
            "reflection": (
                f"已定位目标股票「{_row_label(missing_row)}」，但用户还需要"
                f" {missing_skill} 内容，需继续调用该 skill"
            ),
            "next_skill_hint": missing_skill,
            "next_args_hint": missing_args,
            **_maybe_clear_plan_for_replan("need_more", state),
        }

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
        llm_out: dict[str, Any] = {
            "reflection_verdict": verdict,
            "reflection": obj.get("reason", ""),
            **_maybe_clear_plan_for_replan(verdict, state),
        }
        # Forward the hint to the router — even if the LLM wrote a bad
        # hint, the router validates + falls back to its own LLM call,
        # so this is a pure win when right and a no-op when wrong.
        hint_skill = obj.get("next_skill_hint")
        hint_args = obj.get("next_args_hint")
        if (
            hint_skill
            and isinstance(hint_skill, str)
            and REGISTRY.get_spec(hint_skill)
            and is_skill_necessary_for_query(user_query, hint_skill)
        ):
            llm_out["next_skill_hint"] = hint_skill
            if isinstance(hint_args, dict):
                llm_out["next_args_hint"] = hint_args
        return llm_out
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

_FINANCIAL_SKILLS = {
    "financial-query",
    "news-search",
    "announcement-search",
    "report-search",
}


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
    def _num(row: dict[str, Any], *keys: str) -> float | None:
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


def _rows_from_call(call: ToolCallRecord) -> list[dict[str, Any]]:
    result = call.get("result") or {}
    if not isinstance(result, dict):
        return []
    data = result.get("data") or {}
    if not isinstance(data, dict):
        return []
    rows = (
        data.get("datas")
        or data.get("articles")
        or data.get("announcements")
        or data.get("reports")
        or []
    )
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _requested_followup_skills(user_query: str) -> list[str]:
    """Return detail skills the user explicitly requested, in call order."""
    q = user_query or ""
    has_announcement = explicitly_requests_skill(q, "announcement-search")
    has_report = explicitly_requests_skill(q, "report-search")
    has_news = explicitly_requests_skill(q, "news-search")

    # In "公告或研报", one vertical source is enough; keep the historic
    # announcement-first behavior. In "公告和研报", fetch both, with
    # report first per the product expectation for this chain.
    if has_announcement and has_report:
        if any(k in q for k in ("或", "或者", "二选一", "任一")):
            return ["announcement-search"]
        return ["report-search", "announcement-search"]
    if has_report:
        return ["report-search"]
    if has_announcement:
        return ["announcement-search"]
    if has_news:
        return ["news-search"]
    return []


def _skill_available(name: str) -> bool:
    return bool(REGISTRY.get_spec(name) and REGISTRY.is_enabled(name))


# A specific entity term is a 3+ character Chinese phrase, a 3+ character
# capitalised Latin word, or anything in quotes. These are the things
# that look like company / product / person names and should appear in
# the result rows for the result to be considered "matched" the query.
_ENTITY_TERM_RE = re.compile(
    r"""
    (?P<quoted>      ["'“”](?P<q>[^"'“”]{2,})["'“”])       # "Momenta" / "纵目科技"
    |
    (?P<latin>       \b[A-Z][a-zA-Z]{2,}\b)                # Momenta, AAPL, Tesla
    |
    (?P<cjk>         [一-龥]{3,})                            # 纵目科技, 阿里巴巴
    """,
    re.VERBOSE,
)


def _extract_specific_entity_terms(user_query: str) -> list[str]:
    """Return the specific entity-looking terms in the user query.

    These are the strings that the result rows should mention by name
    for the result to be considered "matched" the query. If the user
    asked about "Momenta 和 纵目科技" and the rows only contain
    "卓目科技" and "纵横科技", the specific terms won't match and
    we can flag the result as low-quality (wrong entity).
    """
    terms: list[str] = []
    for m in _ENTITY_TERM_RE.finditer(user_query or ""):
        if m.group("quoted") is not None:
            terms.append(m.group("q"))
        elif m.group("latin") is not None:
            terms.append(m.group("latin"))
        elif m.group("cjk") is not None:
            terms.append(m.group("cjk"))
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _row_mentions_any_term(row: dict[str, Any], terms: list[str]) -> bool:
    """True when a row's name/code field contains ANY of the terms.

    Used to detect "the data didn't actually match the user's
    entities" — e.g. user asked for 纵目科技 but rows are 卓目科技
    and 纵横科技.
    """
    if not terms:
        return True  # No specific terms to check → assume match
    fields = (
        row.get("股票简称") or row.get("name") or row.get("简称")
        or row.get("股票代码") or row.get("code") or row.get("代码")
        or row.get("title") or row.get("标题") or ""
    )
    if not fields:
        return False
    if not isinstance(fields, str):
        fields = str(fields)
    return any(term and term in fields for term in terms)


def _anysearch_args(user_query: str) -> dict[str, Any]:
    return {
        "action": "search",
        "query": user_query,
        "domain": "finance",
        "max_results": 5,
    }


def _infer_empty_result_recovery(
    *,
    calls: list[ToolCallRecord],
    user_query: str,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Choose a deterministic next step after an empty code-skill result.

    Recovery order:
      1. Retry the same financial skill once with the user's original
         wording when the previous query was a transformed version.
      2. If that has already been tried, use anysearch as low-priority
         finance-domain fallback.
    """
    if not calls:
        return (None, None, None)
    last = calls[-1]
    last_name = str(last.get("name") or "")
    if last_name == "anysearch":
        return (None, None, "anysearch 返回为空，无法继续自动补查")

    if last_name in _FINANCIAL_SKILLS and _skill_available(last_name):
        # ---- Low-quality rows: results returned but no row matches
        # any specific entity in the user's question. This is the
        # "Momenta vs 卓目科技 / 纵横科技" failure mode — the data
        # is non-empty but the entities are wrong. CHECK THIS FIRST
        # because retrying with the original query will just hit the
        # same fuzzy-match failure; the right move is to skip to
        # anysearch (web search typically has the right disambiguation).
        terms = _extract_specific_entity_terms(user_query)
        rows = _rows_from_call(last)
        if terms and rows and not any(
            _row_mentions_any_term(r, terms) for r in rows
        ):
            anysearch_called = any(c.get("name") == "anysearch" for c in calls)
            if not anysearch_called and _skill_available("anysearch"):
                sample_names = ", ".join(
                    str(r.get("股票简称") or r.get("name") or r.get("title") or "?")
                    for r in rows[:3]
                )
                return (
                    "anysearch",
                    _anysearch_args(user_query),
                    (
                        f"{last_name} 返了 {len(rows)} 条但都不含用户问的关键实体"
                        f"（{','.join(terms[:3])}），返回的样例是「{sample_names}」。"
                        "改用 anysearch 联网兜底以正确识别实体"
                    ),
                )

        last_query = str((last.get("args") or {}).get("query") or "").strip()
        original_query = (user_query or "").strip()
        retried_original = any(
            c.get("name") == last_name
            and str((c.get("args") or {}).get("query") or "").strip() == original_query
            for c in calls
        )
        if original_query and last_query != original_query and not retried_original:
            args = dict(last.get("args") or {})
            args["query"] = original_query
            return (
                last_name,
                args,
                f"{last_name} 返回为空，先用用户原始问句换一种问法重试",
            )

    anysearch_called = any(c.get("name") == "anysearch" for c in calls)
    if not anysearch_called and _skill_available("anysearch"):
        return (
            "anysearch",
            _anysearch_args(user_query),
            "金融 Skill 返回为空，改用低优先级 anysearch 联网兜底",
        )

    return (None, None, "工具返回为空数据")


def _find_financial_target(
    calls: list[ToolCallRecord],
    user_query: str,
) -> tuple[int, dict[str, Any]] | tuple[None, None]:
    for idx, call in enumerate(calls):
        if call.get("name") != "financial-query" or not call.get("ok"):
            continue
        target = _pick_top_row(_rows_from_call(call), user_query)
        if target:
            return idx, target
    return None, None


def _target_query_term(row: dict[str, Any]) -> str:
    name = row.get("股票简称") or row.get("简称") or row.get("name") or ""
    code = row.get("股票代码") or row.get("代码") or row.get("code") or ""
    return str(name or code).strip()


def _followup_args_for_skill(skill: str, row: dict[str, Any]) -> dict[str, Any]:
    term = _target_query_term(row)
    if skill == "report-search":
        query = f"{term}的研报" if term else "研报"
    elif skill == "announcement-search":
        query = f"{term}的公告" if term else "公告"
    else:
        query = f"{term}的新闻" if term else "新闻"
    return {"query": query, "limit": "10", "days": "30"}


def _followup_completed(
    calls: list[ToolCallRecord],
    *,
    skill: str,
    financial_idx: int,
    row: dict[str, Any],
) -> bool:
    name = str(row.get("股票简称") or row.get("简称") or row.get("name") or "")
    code = str(row.get("股票代码") or row.get("代码") or row.get("code") or "")
    for call in calls[financial_idx + 1:]:
        if call.get("name") != skill or not call.get("ok"):
            continue
        if not _rows_from_call(call):
            continue
        args_text = json.dumps(call.get("args", {}), ensure_ascii=False)
        if (name and name in args_text) or (code and code in args_text):
            return True
        if not name and not code:
            return True
    return False


def _infer_missing_requested_followup(
    *,
    calls: list[ToolCallRecord],
    user_query: str,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    requested = _requested_followup_skills(user_query)
    if not requested:
        return (None, None, None)

    financial_idx, target_row = _find_financial_target(calls, user_query)
    if financial_idx is None or target_row is None:
        return (None, None, None)

    if not _target_query_term(target_row):
        return (None, None, None)

    for skill in requested:
        if not REGISTRY.get_spec(skill):
            continue
        if not _followup_completed(
            calls,
            skill=skill,
            financial_idx=financial_idx,
            row=target_row,
        ):
            return (skill, _followup_args_for_skill(skill, target_row), target_row)

    return (None, None, None)


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
    # Only financial-query returns a stock universe from which it is safe
    # to pick "the top one". Search result rows can contain generic
    # fields such as author/name="admin"; never treat those as stocks.
    if last_call_name != "financial-query":
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
    query_term = str(name or code)

    # Decide which skill to call next. Priority: 公告 > 研报 > 新闻
    # because for "X 的公告或研报" the user typically wants 公告 first
    # (more immediate, more concrete). We check announcement FIRST so
    # that "公告或研报" routes to announcement-search.
    if any(kw in q for kw in ("公告", "披露", "财报", "季报", "年报", "业绩", "股东",
                              "基本面", "财务", "经营", "公司概况", "资料", "详情",
                              "announcement", "filing", "earnings", "financials")):
        skill = "announcement-search"
        args: dict[str, Any] = {"query": f"{query_term}的公告", "limit": "10", "days": "30"}
    elif any(kw in q for kw in ("研报", "深度", "解读", "券商", "研究报告", "research", "report")):
        skill = "report-search"
        args = {"query": f"{query_term}的研报", "limit": "10", "days": "30"}
    elif any(kw in q for kw in ("新闻", "资讯", "动态", "近况", "news", "latest")):
        skill = "news-search"
        args = {"query": f"{query_term}的新闻", "limit": "10", "days": "30"}
    else:
        # Fallback when the user said e.g. "看看它的最新情况" — pick
        # announcement (broadest of the three).
        skill = "announcement-search"
        args = {"query": f"{query_term}的公告", "limit": "10", "days": "30"}

    # Make sure the target skill is actually registered (e.g. user
    # disabled it in the UI) — bail if not, so the LLM gets a clean
    # chance to handle it.
    if not REGISTRY.get_spec(skill):
        return (None, None, None)

    return (skill, args, target_row)
