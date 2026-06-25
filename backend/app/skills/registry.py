"""In-process ToolRegistry singleton. Skills register themselves on import."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.skills.base import Handler, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

# Cap on how much of a prompt-only skill's body we surface in the
# LLM's system prompt. Keeps a single huge SKILL.md from blowing the
# context window for every chat turn. 4000 chars ~ 1000 CJK tokens.
MAX_PROMPT_BODY_CHARS = 4000


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Handler] = {}
        self._enabled: dict[str, bool] = {}
        # Per-skill prompt body, set by user_uploads for prompt-only
        # skills. Used by to_prompt_text() to inject domain knowledge
        # into the LLM's system prompt.
        self._prompt_bodies: dict[str, str] = {}
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
        self._prompt_bodies.pop(name, None)

    # ----- prompt body (for prompt-only skills) -----
    def set_prompt_body(self, name: str, body: str | None) -> None:
        """Set or clear the prompt body for a skill. Called by
        user_uploads during install/uninstall; consumed by to_prompt_text."""
        if body is None:
            self._prompt_bodies.pop(name, None)
        else:
            self._prompt_bodies[name] = body

    def get_prompt_body(self, name: str) -> str | None:
        return self._prompt_bodies.get(name)

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
        """Human-readable summary injected into the LLM system prompt.

        For code skills (those with parameters) we render a one-line
        per-skill entry — the LLM can call them via tool_call if it
        wants the real data. For prompt-only skills (parameters==[])
        with a stored body, we surface the full body so the LLM has
        the domain knowledge in its context. Bodies are truncated at
        MAX_PROMPT_BODY_CHARS to keep the system prompt bounded.
        """
        lines: list[str] = []
        for s in self.enabled_specs():
            params = ", ".join(
                f"{p.name}{'' if p.required else '?'}: {p.type}" for p in s.parameters
            )
            if params:
                lines.append(f"- {s.name}({params}) — {s.description}")
            else:
                body = self._prompt_bodies.get(s.name)
                if body:
                    truncated = body if len(body) <= MAX_PROMPT_BODY_CHARS else (
                        body[:MAX_PROMPT_BODY_CHARS] + "\n…(已截断)"
                    )
                    lines.append(
                        f"- {s.name} (knowledge) — {s.description}\n{truncated}"
                    )
                else:
                    # Fallback: spec-only entry (no body registered)
                    lines.append(f"- {s.name} — {s.description}")
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
