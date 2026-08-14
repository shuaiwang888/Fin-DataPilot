"""In-process registry for cancellable live agent runs.

Durable status/event records live in SQLite; this registry only owns the
current task and therefore is intentionally cleared on process restart.
"""
from __future__ import annotations

import asyncio


class ActiveRunRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._lock = asyncio.Lock()

    async def register(self, run_id: str, task: asyncio.Task[object]) -> None:
        async with self._lock:
            self._tasks[run_id] = task

    async def unregister(self, run_id: str) -> None:
        async with self._lock:
            self._tasks.pop(run_id, None)

    async def cancel(self, run_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


ACTIVE_RUNS = ActiveRunRegistry()
