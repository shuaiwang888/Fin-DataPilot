"""Verify all 4 core skills register on import and have correct shape."""
from app.skills import REGISTRY


def test_all_four_skills_registered() -> None:
    names = {s.name for s in REGISTRY.list_specs()}
    assert "financial-query" in names
    assert "news-search" in names
    assert "announcement-search" in names
    assert "report-search" in names


def test_financial_query_uses_astock_selector_platform_name() -> None:
    from app.config import get_settings

    mapping = get_settings().iwencai_skill_id_map
    # financial-query (local) → hithink-astock-selector (platform registration)
    assert mapping["financial-query"] == "hithink-astock-selector"


def test_financial_query_description_exposes_calling_rules() -> None:
    spec = REGISTRY.get_spec("financial-query")
    assert spec is not None
    assert "未明确标的类型时默认按股票处理" in spec.description
    assert "所有筛选条件必须同时放在同一个" in spec.description
    assert "指标超过 5 个时拆成多个" in spec.description
    assert "不得包含“资金面”" in spec.description


def test_skill_specs_have_descriptions() -> None:
    for spec in REGISTRY.list_specs():
        assert spec.description.strip(), f"{spec.name} has empty description"
        assert spec.display_name.strip(), f"{spec.name} has empty display_name"
        assert spec.parameters, f"{spec.name} has no parameters"
