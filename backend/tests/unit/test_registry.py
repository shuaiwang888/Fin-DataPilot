"""Unit tests for the ToolRegistry and skill registration."""
from __future__ import annotations

import pytest

from app.skills.base import Handler, ToolParameter, ToolResult, ToolSpec
from app.skills.registry import ToolRegistry


async def _echo_handler(**kwargs) -> ToolResult:
    return ToolResult(tool="echo", ok=True, data=kwargs)


@pytest.fixture
def reg() -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        ToolSpec(
            name="echo",
            display_name="Echo",
            description="Echoes args",
            category="test",
            parameters=[
                ToolParameter(name="text", type="string", description="text to echo"),
            ],
        ),
        _echo_handler,
    )
    return r


async def test_register_and_list(reg: ToolRegistry) -> None:
    specs = reg.list_specs()
    assert len(specs) == 1
    assert specs[0].name == "echo"


async def test_dispatch_filters_args(reg: ToolRegistry) -> None:
    result = await reg.dispatch("echo", {"text": "hi", "injected": "nope"})
    assert result.ok
    assert result.data == {"text": "hi"}


async def test_dispatch_unknown_tool(reg: ToolRegistry) -> None:
    result = await reg.dispatch("does-not-exist", {})
    assert not result.ok
    assert "unknown" in (result.error or "")


async def test_dispatch_disabled_tool(reg: ToolRegistry) -> None:
    reg.set_enabled("echo", False)
    result = await reg.dispatch("echo", {"text": "hi"})
    assert not result.ok
    assert "disabled" in (result.error or "")


def test_to_openai_tools(reg: ToolRegistry) -> None:
    tools = reg.to_openai_tools()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "echo"
    assert "text" in tools[0]["function"]["parameters"]["properties"]


def test_register_duplicate_raises(reg: ToolRegistry) -> None:
    with pytest.raises(ValueError):
        reg.register(
            ToolSpec(
                name="echo",
                display_name="x",
                description="x",
                category="x",
                parameters=[],
            ),
            _echo_handler,
        )
