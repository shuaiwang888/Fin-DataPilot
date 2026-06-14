"""Skill framework: base types, registry, and skill registrations.

Importing this package triggers registration of all skills. The four core
skills live in their own modules to keep this file small.
"""
from __future__ import annotations

# Order matters: register base types / registry first
from app.skills.base import (  # noqa: F401
    Handler,
    ToolParameter,
    ToolResult,
    ToolSpec,
    timed,
)
from app.skills.registry import REGISTRY  # noqa: F401

# Concrete skills (each one calls REGISTRY.register(...) at import time)
from app.skills import (  # noqa: F401
    financial_query,
    news_search,
    announcement_search,
    report_search,
)

__all__ = ["REGISTRY", "ToolSpec", "ToolResult", "ToolParameter", "Handler", "timed"]
