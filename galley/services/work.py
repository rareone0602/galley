"""Long jobs that must not block an HTTP request.

Compiling this paper takes tens of seconds and `latexdiff` on it takes minutes.
Holding a request open for that means a browser timeout loses the result and a
backend restart loses the work, so both run here instead and the UI asks how
they are getting on.

Deliberately no caching. A stale "marked up" PDF would show you the wrong
change, which is the one failure this whole application exists to prevent — so
a second request while one is running joins it, and a request after one has
finished starts a fresh run.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Slot:
    state: str = "idle"  # idle | running | done | failed
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def as_dict(self) -> dict:
        elapsed = None
        if self.started_at:
            elapsed = round((self.finished_at or time.time()) - self.started_at, 1)
        body = {"state": self.state, "elapsed_seconds": elapsed}
        if self.state == "done" and isinstance(self.result, dict):
            body.update(self.result)
        if self.error:
            body["error"] = self.error
        return body


class WorkTable:
    """One slot per key. Asking again while it runs joins the run in progress."""

    def __init__(self) -> None:
        self._slots: dict[str, Slot] = {}

    def state(self, key: str) -> dict:
        return self._slots.get(key, Slot()).as_dict()

    def start(self, key: str, fn: Callable[[], Any]) -> dict:
        slot = self._slots.get(key)
        if slot and slot.state == "running":
            return slot.as_dict()

        slot = Slot(state="running", started_at=time.time())
        self._slots[key] = slot

        async def run() -> None:
            try:
                slot.result = await asyncio.to_thread(fn)
                slot.state = "done"
            except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
                slot.error = str(exc)
                slot.state = "failed"
            finally:
                slot.finished_at = time.time()

        slot.task = asyncio.create_task(run(), name=f"galley-work-{key}")
        return slot.as_dict()

    async def shutdown(self) -> None:
        for slot in self._slots.values():
            if slot.task and not slot.task.done():
                slot.task.cancel()
