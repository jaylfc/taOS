from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DesktopCommand:
    """A single instruction for a user's desktop (open an app, move a window…).

    `kind` is the wire discriminator the browser switches on:
      "open-app" -> dispatched as a `taos:open-app` CustomEvent
      "window"   -> dispatched as a `taos:window` CustomEvent (a WindowOp)
    `payload` carries the event detail verbatim.
    """

    kind: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)


class DesktopCommandBroker:
    """In-memory pub/sub for desktop commands, one channel per user_id.

    Mirrors ProjectEventBroker but with NO replay buffer: a command is a
    one-shot side effect (open this app, move that window). Replaying buffered
    commands to a freshly-connected desktop would re-open apps the user already
    closed, so a new subscriber starts from empty and only sees commands emitted
    after it connects.

    Single-worker assumption: publishers (agent tools) and subscribers (the
    desktop SSE stream) share one process, same as the canvas broker.
    """

    # Per-subscriber queues are bounded so a stalled/dead SSE consumer can't grow
    # memory without limit. On overflow we drop the OLDEST command (a desktop
    # that far behind has already missed the UI state these describe; the newest
    # commands are the ones worth keeping).
    _MAX_QUEUE = 128

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[DesktopCommand]]] = {}
        self._lock = asyncio.Lock()
        # Pending screenshot (and other request/response) round-trips, keyed by
        # request_id. The agent endpoint registers a future, emits a command
        # carrying the request_id, and awaits; the desktop POSTs the result back
        # which resolves the future.
        self._results: dict[str, asyncio.Future[Any]] = {}
        # Owner user_id per pending request, so a result can only be resolved by
        # the user who initiated it (defence in depth on top of the unguessable
        # request_id, which is only ever delivered to that user's desktops).
        self._result_owner: dict[str, str] = {}

    def register_result(self, request_id: str, owner_user_id: str) -> asyncio.Future[Any]:
        """Register a pending result future for request_id and return it."""
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._results[request_id] = fut
        self._result_owner[request_id] = owner_user_id
        return fut

    def resolve_result(self, request_id: str, value: Any, *, user_id: str | None = None) -> bool:
        """Resolve a pending result. Returns False if no one is waiting, or if
        user_id is given and does not own the request."""
        if user_id is not None and self._result_owner.get(request_id) != user_id:
            return False
        fut = self._results.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(value)
        return True

    def discard_result(self, request_id: str) -> None:
        self._results.pop(request_id, None)
        self._result_owner.pop(request_id, None)

    async def subscribe(self, user_id: str) -> asyncio.Queue[DesktopCommand]:
        queue: asyncio.Queue[DesktopCommand] = asyncio.Queue(maxsize=self._MAX_QUEUE)
        async with self._lock:
            self._queues.setdefault(user_id, []).append(queue)
        return queue

    async def unsubscribe(self, user_id: str, queue: asyncio.Queue[DesktopCommand]) -> None:
        async with self._lock:
            qs = self._queues.get(user_id, [])
            if queue in qs:
                qs.remove(queue)
            if not qs:
                self._queues.pop(user_id, None)

    async def emit(self, user_id: str, command: DesktopCommand) -> int:
        """Fan a command out to every open desktop for `user_id`.

        Returns the number of live subscribers it reached (0 means the user has
        no desktop connected right now — the caller may want to surface that).
        """
        async with self._lock:
            qs = list(self._queues.get(user_id, []))
            for q in qs:
                if q.full():
                    try:
                        q.get_nowait()  # drop the oldest to make room for the newest
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(command)
        return len(qs)
