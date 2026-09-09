"""In-process publish/subscribe, so one agent's messages reach every open tab.

Subscribers get their own bounded queue. A slow reader drops its oldest events
rather than stalling the agent that is producing them — the durable copy is in
the `events` table, and the UI replays from there on reconnect.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

QUEUE_MAX = 512


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    @asynccontextmanager
    async def subscribe(self, topic: str) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subs[topic].add(queue)
        try:
            yield queue
        finally:
            self._subs[topic].discard(queue)
            if not self._subs[topic]:
                self._subs.pop(topic, None)

    async def publish(self, topic: str, event: Any) -> None:
        for queue in list(self._subs.get(topic, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subs.get(topic, ()))
