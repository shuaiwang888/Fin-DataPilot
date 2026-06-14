"""ToolSpec / ToolResult base types. The agent layer speaks only these types;
individual skill implementations adapt the iWencai CLI / HTTP API into them."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field


# ---------- Parameter schema ----------


class ToolParameter(BaseModel):
    name: str
    type: Literal["string", "number", "integer", "boolean", "object", "array"]
    description: str
    required: bool = True
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None

    def to_json_schema(self) -> dict[str, Any]:
        """Convert this parameter to a JSON-Schema fragment for the LLM tool call."""
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.type == "array" and self.items is not None:
            schema["items"] = self.items
        if self.type == "object" and self.properties is not None:
            schema["properties"] = self.properties
        return schema


# ---------- Spec ----------


class ToolSpec(BaseModel):
    name: str
    display_name: str
    description: str
    category: str
    parameters: list[ToolParameter]
    returns_schema: dict[str, Any] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)
    enabled_by_default: bool = True
    version: str = "0.1.0"
    examples: list[dict[str, Any]] = Field(default_factory=list)

    def to_openai_tool(self) -> dict[str, Any]:
        """Render as an OpenAI-style function-calling tool entry."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            properties[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


# ---------- Result ----------


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any | None = None
    error: str | None = None
    trace_id: str = ""
    duration_ms: int = 0
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "trace_id": self.trace_id,
            "duration_ms": self.duration_ms,
            "meta": self.meta or {},
        }


# ---------- Handler type ----------


Handler = Callable[..., Awaitable[ToolResult]]


# ---------- Timing helper ----------


async def timed(tool: str, coro_factory: Callable[[], Awaitable[ToolResult]]) -> ToolResult:
    """Run a handler coroutine, attach timing and a fresh trace_id."""
    trace_id = time.strftime("%Y%m%d%H%M%S-") + hex(int(time.time() * 1e6) % (1 << 32))[2:]
    t0 = time.perf_counter()
    try:
        result = await coro_factory()
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            tool=tool,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            trace_id=trace_id,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    result.tool = tool
    result.trace_id = trace_id
    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    return result
