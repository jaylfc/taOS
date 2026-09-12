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
    id: str | None = None


class ProjectEventBroker:
    """In-memory pub/sub. One channel per project_id.

    Single-worker assumption: all subscribers and publishers share one process.
    See spec s4 -- multi-worker is out of scope.
    """

    def __init__(self, replay_size: int = 32) -> None:
        self._replay_size = replay_size
        self._next_id = 0
        self._queues: dict[str, list[asyncio.Queue[ProjectEvent]]] = {}
        self._replay: dict[str, deque[ProjectEvent]] = {}
        self._lock = asyncio.Lock()
        self._queue_maxsize = 256

    async def subscribe(
        self,
        project_id: str,
        last_event_id: str | None = None,
    ) -> asyncio.Queue[ProjectEvent]:
        queue: asyncio.Queue[ProjectEvent] = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._queues.setdefault(project_id, []).append(queue)
            last_id: int | None = None
            if last_event_id is not None:
                try:
                    last_id = int(last_event_id)
                except ValueError:
                    last_id = None
            for ev in self._replay.get(project_id, ()):
                if last_id is not None and ev.id is not None and int(ev.id) <= last_id:
                    continue
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

    async def publish(self, project_id: str, event: ProjectEvent) -> None:
        async with self._lock:
            event.id = str(self._next_id)
            self._next_id += 1
            buf = self._replay.setdefault(project_id, deque(maxlen=self._replay_size))
            buf.append(event)
            queues = list(self._queues.get(project_id, []))
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
