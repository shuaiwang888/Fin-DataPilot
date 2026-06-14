"""In-process ToolRegistry singleton. Skills register themselves on import."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.skills.base import Handler, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Handler] = {}
        self._enabled: dict[str, bool] = {}
        self._lock = asyncio.Lock()

    # ----- registration -----
    def register(self, spec: ToolSpec, handler: Handler) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool '{spec.name}' already registered")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        self._enabled[spec.name] = spec.enabled_by_default
        logger.info("Registered skill: %s (%s)", spec.name, spec.display_name)

    def unregister(self, name: str) -> None:
        self._specs.pop(name, None)
        self._handlers.pop(name, None)
        self._enabled.pop(name, None)

    # ----- queries -----
    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)

    def enabled_specs(self) -> list[ToolSpec]:
        return [s for s in self._specs.values() if self._enabled.get(s.name, False)]

    # ----- enable/disable (from user prefs) -----
    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._specs:
            raise KeyError(name)
        self._enabled[name] = enabled

    def enable_all(self) -> None:
        for name in self._specs:
            self._enabled[name] = True

    # ----- LLM-facing renderers -----
    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [s.to_openai_tool() for s in self.enabled_specs()]

    def to_prompt_text(self) -> str:
        """Human-readable summary injected into the LLM system prompt."""
        lines: list[str] = []
        for s in self.enabled_specs():
            params = ", ".join(
                f"{p.name}{'' if p.required else '?'}: {p.type}" for p in s.parameters
            )
            lines.append(f"- {s.name}({params}) — {s.description}")
        return "\n".join(lines)

    def to_introspection(self) -> list[dict[str, Any]]:
        return [s.model_dump() for s in self._specs.values()]

    # ----- dispatch -----
    async def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self._handlers:
            return ToolResult(tool=name, ok=False, error=f"unknown skill '{name}'")
        if not self._enabled.get(name, False):
            return ToolResult(tool=name, ok=False, error=f"skill '{name}' is disabled")
        handler = self._handlers[name]
        # Validate args against spec
        spec = self._specs[name]
        allowed = {p.name for p in spec.parameters}
        filtered = {k: v for k, v in args.items() if k in allowed}
        return await handler(**filtered)


# Global singleton
REGISTRY = ToolRegistry()
