"""In-process ToolRegistry singleton. Skills register themselves on import."""
from __future__ import annotations

import asyncio
import logging
import re
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
                        f"- {s.name} (untrusted reference material) — {s.description}\n"
                        "<untrusted_skill_reference>\n"
                        f"{truncated}\n"
                        "</untrusted_skill_reference>"
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
        spec = self._specs[name]
        try:
            filtered = _validate_args(spec, args)
        except ValueError as exc:
            return ToolResult(
                tool=name,
                ok=False,
                error=f"INVALID_ARGUMENT: {exc}",
                meta={"error_code": "INVALID_ARGUMENT"},
            )
        try:
            result = await handler(**filtered)
        except Exception as exc:  # noqa: BLE001
            logger.exception("skill %s raised while dispatching", name)
            return ToolResult(
                tool=name,
                ok=False,
                error=f"SKILL_EXECUTION_ERROR: {type(exc).__name__}: {exc}",
                meta={"error_code": "SKILL_EXECUTION_ERROR"},
            )
        if result.ok:
            try:
                _validate_result(spec, result)
            except ValueError as exc:
                logger.error("skill %s returned an invalid result: %s", name, exc)
                return ToolResult(
                    tool=name,
                    ok=False,
                    error=f"INVALID_RESULT: {exc}",
                    trace_id=result.trace_id,
                    duration_ms=result.duration_ms,
                    meta={"error_code": "INVALID_RESULT"},
                )
        return result


def _validate_args(spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Validate the public ToolSpec contract before reaching vendor code.

    We intentionally reject unknown fields instead of silently dropping them:
    a planner bug must be observable, especially when it changes a financial
    query's time window or universe.
    """
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    by_name = {p.name: p for p in spec.parameters}
    unknown = sorted(set(args) - set(by_name))
    if unknown:
        raise ValueError(f"unknown parameter(s): {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    for name, p in by_name.items():
        value = args.get(name, p.default)
        if value is None:
            if p.required:
                raise ValueError(f"missing required parameter '{name}'")
            continue
        if p.type == "string":
            if not isinstance(value, str):
                raise ValueError(f"parameter '{name}' must be a string")
            value = value.strip()
            if p.required and not value:
                raise ValueError(f"parameter '{name}' must not be empty")
            if p.min_length is not None and len(value) < p.min_length:
                raise ValueError(f"parameter '{name}' is too short")
            if p.max_length is not None and len(value) > p.max_length:
                raise ValueError(f"parameter '{name}' exceeds {p.max_length} characters")
            if p.pattern is not None and not re.fullmatch(p.pattern, value):
                raise ValueError(f"parameter '{name}' has an invalid format")
        elif p.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"parameter '{name}' must be an integer")
        elif p.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"parameter '{name}' must be a number")
        elif p.type == "boolean" and not isinstance(value, bool):
            raise ValueError(f"parameter '{name}' must be a boolean")
        elif p.type == "array" and not isinstance(value, list):
            raise ValueError(f"parameter '{name}' must be an array")
        elif p.type == "object" and not isinstance(value, dict):
            raise ValueError(f"parameter '{name}' must be an object")
        if p.enum is not None and value not in p.enum:
            raise ValueError(f"parameter '{name}' must be one of {p.enum}")
        if p.type in {"integer", "number"}:
            if p.ge is not None and value < p.ge:
                raise ValueError(f"parameter '{name}' must be >= {p.ge}")
            if p.le is not None and value > p.le:
                raise ValueError(f"parameter '{name}' must be <= {p.le}")
        clean[name] = value
    return clean


def _validate_result(spec: ToolSpec, result: ToolResult) -> None:
    """Small, dependency-free JSON-schema subset for every successful tool."""
    if result.data is None:
        raise ValueError("successful result must contain data")
    schema = spec.returns_schema
    if not schema:
        return
    data = result.data
    if schema.get("type") == "object" and not isinstance(data, dict):
        raise ValueError("data must be an object")
    if schema.get("type") == "array" and not isinstance(data, list):
        raise ValueError("data must be an array")
    if isinstance(data, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"missing result field(s): {', '.join(missing)}")
        for key, rule in (schema.get("properties", {}) or {}).items():
            if key not in data:
                continue
            expected = rule.get("type") if isinstance(rule, dict) else None
            value = data[key]
            if expected == "array" and not isinstance(value, list):
                raise ValueError(f"result field '{key}' must be an array")
            if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"result field '{key}' must be an integer")


# Global singleton
REGISTRY = ToolRegistry()
