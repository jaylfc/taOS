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
    See spec §4 — multi-worker is out of scope.
    """

    def __init__(self, replay_size: int = 32) -> None:
        self._replay_size = replay_size
        self._queues: dict[str, list[asyncio.Queue[ProjectEvent]]] = {}
        self._replay: dict[str, deque[ProjectEvent]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, project_id: str) -> asyncio.Queue[ProjectEvent]:
        queue: asyncio.Queue[ProjectEvent] = asyncio.Queue()
        async with self._lock:
            self._queues.setdefault(project_id, []).append(queue)
            for ev in self._replay.get(project_id, ()):
                queue.put_nowait(ev)
        return queue

    async def unsubscribe(self, project_id: str, queue: asyncio.Queue[ProjectEvent]) -> None:
        async with self._lock:
            qs = self._queues.get(project_id, [])
            if queue in qs:
                qs.remove(queue)

    async def publish(self, project_id: str, event: ProjectEvent) -> None:
        async with self._lock:
            buf = self._replay.setdefault(project_id, deque(maxlen=self._replay_size))
            buf.append(event)
            for q in list(self._queues.get(project_id, [])):
                q.put_nowait(event)
