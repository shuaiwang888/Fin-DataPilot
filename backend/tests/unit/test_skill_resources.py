"""Tests for bounded, project-owned Skill resource loading."""

from __future__ import annotations

from app.skills.resources import load_skill_resources, render_skill_resources


def test_loads_checked_in_skill_instructions() -> None:
    resources = load_skill_resources(["financial-query"], max_chars=2_000)

    assert "financial-query" in resources
    assert resources["financial-query"]["instruction"]


def test_refuses_path_traversal_and_unknown_directories() -> None:
    resources = load_skill_resources(["../../.env", "not-a-real-skill"])

    assert resources == {}


def test_rendered_resources_are_bounded() -> None:
    resources = load_skill_resources(["financial-query", "news-search"], max_chars=2_000)
    rendered = render_skill_resources(resources, max_chars=100)

    assert len(rendered) <= 100
    assert "financial-query" in rendered
