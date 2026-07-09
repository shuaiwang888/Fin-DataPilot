"""Smoke tests for the bundled anysearch-skill wrapper.

We don't actually hit the network (CI is offline-friendly); we verify
the argv builder produces sane CLI commands and the action router
rejects garbage. End-to-end real-API checks live in the manual
TEST_PLAN.md inside Skills/anysearch-skill/.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.skills.anysearch import _build_argv, ACTIONS


# --- argv builder ----------------------------------------------------


def test_action_search_minimal() -> None:
    argv = _build_argv("search", {"query": "宁德时代"})
    assert argv == ["search", "宁德时代"]


def test_action_search_vertical() -> None:
    argv = _build_argv(
        "search",
        {
            "query": "AAPL",
            "max_results": 5,
            "domain": "finance",
            "sub_domain": "finance.quote",
            "sub_domain_params": "type=stock,symbol=AAPL,cn_code=",
        },
    )
    assert argv == [
        "search", "AAPL",
        "--max_results", "5",
        "--domain", "finance",
        "--sub_domain", "finance.quote",
        "--sdp", "type=stock,symbol=AAPL,cn_code=",
    ]


def test_action_search_ignores_blank_or_invalid_max_results() -> None:
    assert _build_argv("search", {"query": "Momenta", "max_results": ""}) == [
        "search", "Momenta",
    ]
    assert _build_argv("search", {"query": "Momenta", "max_results": "many"}) == [
        "search", "Momenta",
    ]


def test_action_search_clamps_max_results() -> None:
    assert _build_argv("search", {"query": "Momenta", "max_results": 99}) == [
        "search", "Momenta", "--max_results", "10",
    ]
    assert _build_argv("search", {"query": "Momenta", "max_results": 0}) == [
        "search", "Momenta", "--max_results", "1",
    ]


def test_action_search_missing_query() -> None:
    assert _build_argv("search", {"query": ""}) is None
    assert _build_argv("search", {}) is None


def test_action_extract() -> None:
    argv = _build_argv("extract", {"url": "https://example.com/x"})
    assert argv == ["extract", "https://example.com/x"]


def test_action_extract_missing_url() -> None:
    assert _build_argv("extract", {"url": ""}) is None


def test_action_batch_search_with_queries_json() -> None:
    argv = _build_argv(
        "batch_search",
        {
            "queries_json": '[{"query":"a"},{"query":"b"}]',
            "domain": "finance",
            "sub_domain": "finance.quote",
        },
    )
    assert argv == [
        "batch_search",
        "--queries", '[{"query":"a"},{"query":"b"}]',
        "--domain", "finance",
        "--sub_domain", "finance.quote",
    ]


def test_action_batch_search_normalises_string_array_queries() -> None:
    argv = _build_argv(
        "batch_search",
        {
            "queries_json": '["昨天 A股 V字走势 原因","昨天 A股 午后拉升 领涨板块"]',
            "max_results": "5",
            "sub_domain": "#finance",
        },
    )

    assert argv == [
        "batch_search",
        "--queries",
        '[{"query":"昨天 A股 V字走势 原因","max_results":5},'
        '{"query":"昨天 A股 午后拉升 领涨板块","max_results":5}]',
        "--domain", "finance",
    ]


def test_action_batch_search_accepts_python_list() -> None:
    argv = _build_argv(
        "batch_search",
        {
            "queries_json": ["AAPL news", {"query": "MSFT earnings", "domain": "finance"}],
            "max_results": 3,
        },
    )

    assert argv == [
        "batch_search",
        "--queries",
        '[{"query":"AAPL news","max_results":3},'
        '{"query":"MSFT earnings","domain":"finance","max_results":3}]',
    ]


def test_action_batch_search_rejects_invalid_json() -> None:
    assert _build_argv("batch_search", {"queries_json": "not-json"}) is None
    assert _build_argv("batch_search", {"queries_json": "{}"}) is None  # not a list
    assert _build_argv("batch_search", {"queries_json": "[]"}) is None  # empty


def test_action_get_sub_domains_single() -> None:
    argv = _build_argv("get_sub_domains", {"domain": "finance"})
    assert argv == ["get_sub_domains", "--domain", "finance"]


def test_action_get_sub_domains_multi() -> None:
    argv = _build_argv("get_sub_domains", {"domains": "finance,health"})
    assert argv == ["get_sub_domains", "--domains", "finance,health"]


def test_action_get_sub_domains_missing() -> None:
    assert _build_argv("get_sub_domains", {}) is None
    assert _build_argv("get_sub_domains", {"domain": "", "domains": ""}) is None


def test_action_unknown_returns_none() -> None:
    assert _build_argv("hack", {"query": "x"}) is None


def test_actions_enum_is_complete() -> None:
    """The 4 actions documented in SKILL.md must all be in the enum."""
    assert set(ACTIONS) == {"search", "extract", "batch_search", "get_sub_domains"}


# --- registry integration --------------------------------------------


def test_anysearch_registered_when_skill_present() -> None:
    """The bundled skill is at Skills/anysearch-skill/ in this repo,
    so it should be auto-registered on import."""
    from app.skills.registry import REGISTRY
    spec = REGISTRY.get_spec("anysearch")
    assert spec is not None
    assert spec.category == "search"
    assert spec.enabled_by_default is True
    # Action must be the first / required parameter so the LLM picks it.
    assert spec.parameters[0].name == "action"
    assert spec.parameters[0].required is True
    assert "search" in spec.parameters[0].enum


def test_anysearch_spec_description_mentions_finance() -> None:
    from app.skills.registry import REGISTRY
    spec = REGISTRY.get_spec("anysearch")
    assert spec is not None
    # The hint string we expose to the LLM should call out the
    # 'get_sub_domains first' pattern so the router uses vertical search
    # for finance queries.
    assert "get_sub_domains" in spec.description
    assert "finance" in spec.description


# --- handler-level defensive try/except --------------------------------


@pytest.mark.asyncio
async def test_handler_returns_failed_result_when_argv_build_raises() -> None:
    """Regression for the HF Space crash: if _build_argv raises any
    unexpected exception (e.g. int('') on a future LLM input), the
    handler must catch it and return ok=False — never let the
    exception propagate out of the handler and abort the whole
    graph run."""
    from app.skills.anysearch import anysearch_handler

    # Force _build_argv to raise
    with patch("app.skills.anysearch._build_argv", side_effect=ValueError("int(''): boom")):
        result = await anysearch_handler(action="search", query="test")
    assert result.ok is False
    assert "argv" in result.error
    assert "ValueError" in result.error
    assert "int(''): boom" in result.error
    assert result.tool == "anysearch"


@pytest.mark.asyncio
async def test_handler_max_results_empty_string_does_not_crash() -> None:
    """Direct regression for the reported bug: LLM passes
    max_results='' (empty string). The old code did int('') and
    ValueError bubbled out of the handler, killing the whole
    agent graph. The fixed code uses _coerce_max_results which
    returns None for ''."""
    from app.skills.anysearch import anysearch_handler

    # Stub the subprocess to avoid actually running the CLI.
    with patch("app.skills.anysearch.asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"some output\n", b""))
        proc.returncode = 0
        mock_exec.return_value = proc

        # action=search + query present + max_results='' — used to crash
        result = await anysearch_handler(
            action="search", query="test", max_results="",
        )
    # We expect the call to succeed (or fail gracefully), NOT raise.
    assert isinstance(result.ok, bool)
    # The CLI was invoked with argv that omits --max_results (since
    # coerce returned None for '').
    called_argv = mock_exec.call_args.args
    assert "--max_results" not in called_argv, (
        f"empty max_results should omit --max_results, got argv: {called_argv}"
    )
