"""One public event contract shared by transport, persistence and recovery."""
from __future__ import annotations

from typing import Any

# Tokens are transient: the canonical message_final contains the full answer.
# Keeping them out of SQLite avoids rewriting the entire JSON journal per token.
TRANSIENT_EVENTS = frozenset({"token_delta", "heartbeat", "ping"})
PRIVATE_EVENTS = frozenset({"think_chunk", "think_done"})


class RunTranscript:
    """Collect compact presentation metadata alongside the tool audit trail."""

    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.answer: dict[str, Any] = {}
        self.final_text = ""
        self.error: str | None = None

    def collect(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        data = event.get("data", {})
        if name in {"tool_call", "tool_result"}:
            index = next((i for i, call in enumerate(self.tool_calls) if call.get("trace_id") == data.get("trace_id")), None)
            if index is None:
                self.tool_calls.append(data)
            else:
                self.tool_calls[index] = {**self.tool_calls[index], **data}
        elif name in {"think", "reflection", "plan", "phase"}:
            self.steps.append(event)
            self.steps = self.steps[-64:]
        elif name == "error":
            self.error = str(data.get("message") or "任务未完成")
        elif name == "message_final":
            self.final_text = str(data.get("content") or "")
            if isinstance(data.get("tool_calls"), list):
                self.tool_calls = data["tool_calls"]
            self.answer = {key: data[key] for key in ("presentation", "preamble", "citations") if key in data}

    def metadata(self) -> dict[str, Any]:
        return {"trace": self.tool_calls, "events": self.steps, **self.answer}
