from __future__ import annotations
import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectEvent:
    kind: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)


class ProjectEventBroker:
    """In-memory pub/sub. One channel per project_id.

    Single-worker assumption: all subscribers and publishers share one process.
    See spec s4 -- multi-worker is out of scope.
    """

    def __init__(self, replay_size: int = 32) -> None:
        self._replay_size = replay_size
        self._queues: dict[str, list[asyncio.Queue[ProjectEvent]]] = {}
        self._replay: dict[str, deque[ProjectEvent]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, project_id: str) -> asyncio.Queue[ProjectEvent]:
        queue: asyncio.Queue[ProjectEvent] = asyncio.Queue(maxsize=self._replay_size)
        async with self._lock:
            self._queues.setdefault(project_id, []).append(queue)
            for ev in self._replay.get(project_id, ()):
                try:
                    queue.put_nowait(ev)
                except asyncio.QueueFull:
                    break
        return queue

    async def unsubscribe(self, project_id: str, queue: asyncio.Queue[ProjectEvent]) -> None:
        async with self._lock:
            qs = self._queues.get(project_id, [])
            if queue in qs:
                qs.remove(queue)
            if not qs:
                self._queues.pop(project_id, None)
                # Keep _replay so a reconnecting subscriber (e.g. a reopened
                # SSE connection) can catch up on events it missed. The deque
                # has a fixed maxlen so memory stays bounded.

    async def publish(self, project_id: str, event: ProjectEvent) -> None:
        async with self._lock:
            buf = self._replay.setdefault(project_id, deque(maxlen=self._replay_size))
            buf.append(event)
            queues = list(self._queues.get(project_id, []))
        # Backpressure policy: do not block the broker (and every other
        # subscriber) on a slow consumer. If a subscriber's bounded queue is
        # full, evict its oldest item and retry so the consumer stays on the
        # live stream rather than stalling indefinitely.
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass
