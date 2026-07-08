"""Smoke tests for the bundled anysearch-skill wrapper.

We don't actually hit the network (CI is offline-friendly); we verify
the argv builder produces sane CLI commands and the action router
rejects garbage. End-to-end real-API checks live in the manual
TEST_PLAN.md inside Skills/anysearch-skill/.
"""
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
