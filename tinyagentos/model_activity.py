"""Model Activity feed -- a controller-local ring buffer of model-level events.

The scheduler (model load / unload / eviction / shrink) and the LLM gateway
(request lifecycle, backend failover) record events here.  A bounded ring
keeps memory flat on constrained devices (RK3588 class) and the same records
are fanned out to SSE subscribers so the Activity app can show a live feed.

Wiring status: the gateway hooks fire on every proxied request.  The scheduler
hooks fire for any ``CoreAwareModelScheduler`` constructed with this feed
(``activity_feed=...`` or ``set_activity_feed(...)``); nothing in
``create_app`` constructs one yet, so that half stays quiet until the Phase-1.5
sequential-loading wiring (#172) lands.  Both halves are covered by
``tests/test_model_activity.py``.

Event vocabulary (stable -- the desktop feed and any consumer switch on these
strings):

    model.load      a model became resident on a backend
    model.unload    a resident model was released
    model.evict     a resident was evicted to free resources for another load
    model.shrink    a resident's resource mask was reduced (reload)
    model.route     the gateway changed the backend serving a model
    request.start   the proxy began an inference request against a backend
    request.finish  the proxy finished one (carries duration, tokens, rate)

Visibility: an event may carry an ``owner`` -- the principal the event is
attributable to (the gateway stamps ``user:<id>``, an agent's registry name, or
the gateway master-key label; a controller-level event such as a scheduler
model load has none).  The HTTP surface filters on it: a member session only
ever sees events its own principal owns, an admin sees the whole ring.  See
``routes/model_activity.py::_owner_scope``.

The feed is deliberately in-process and non-durable: it is an operational
window ("what happened just now"), not a system of record.  The existing
``SystemEventStore`` remains the durable log.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

MODEL_LOAD = "model.load"
MODEL_UNLOAD = "model.unload"
MODEL_EVICT = "model.evict"
MODEL_SHRINK = "model.shrink"
MODEL_ROUTE = "model.route"
REQUEST_START = "request.start"
REQUEST_FINISH = "request.finish"

EVENT_TYPES: tuple[str, ...] = (
    MODEL_LOAD,
    MODEL_UNLOAD,
    MODEL_EVICT,
    MODEL_SHRINK,
    MODEL_ROUTE,
    REQUEST_START,
    REQUEST_FINISH,
)

#: The controller hosts the models it serves locally.
CONTROLLER_WORKER = "controller"


@dataclass(frozen=True)
class ModelActivityEvent:
    """One immutable record in the ring buffer.

    ``worker`` is the node the event happened on -- ``"controller"`` for the
    local host, a worker name for cluster-attached workers.  ``token_rate`` is
    output tokens per second, only meaningful on ``request.finish``.  ``owner``
    is the principal the event is attributable to (``user:<id>``, an agent's
    registry name, ...) or ``None`` for a controller-level event that belongs
    to no caller; the HTTP surface scopes on it so one session user cannot read
    another principal's activity.
    """

    seq: int
    ts: float
    event: str
    model: str
    worker: str = CONTROLLER_WORKER
    owner: str | None = None
    backend: str = ""
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    token_rate: float | None = None
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the HTTP/SSE surface (stable key names)."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "model": self.model,
            "worker": self.worker,
            "owner": self.owner,
            "backend": self.backend,
            "duration_ms": self.duration_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "token_rate": self.token_rate,
            "reason": self.reason,
            # Copy: this record is frozen, and a caller mutating the returned
            # dict must not be able to reach back into it.
            "detail": dict(self.detail),
        }


class ModelActivityFeed:
    """Bounded ring buffer of :class:`ModelActivityEvent` with fan-out.

    ``record()`` is synchronous on purpose: the scheduler's event hook runs in
    sync code and must not need an event loop to publish.  Fan-out uses
    ``put_nowait`` with drop-oldest overflow, so one stalled SSE client can
    never block a producer or grow memory without bound.
    """

    def __init__(self, maxlen: int = 500, subscriber_maxlen: int = 256) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        if subscriber_maxlen < 1:
            raise ValueError("subscriber_maxlen must be >= 1")
        self._ring: deque[ModelActivityEvent] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue[ModelActivityEvent]] = set()
        self._subscriber_maxlen = subscriber_maxlen
        self._seq = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        event: str,
        *,
        model: str,
        worker: str = CONTROLLER_WORKER,
        owner: str | None = None,
        backend: str = "",
        duration_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        token_rate: float | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        ts: float | None = None,
    ) -> ModelActivityEvent:
        """Append one event to the ring and fan it out to subscribers.

        ``owner`` is the principal the event is attributable to; it is the key
        the read surface scopes on, so a producer that knows the caller must
        pass it.
        """
        self._seq += 1
        ev = ModelActivityEvent(
            seq=self._seq,
            ts=time.time() if ts is None else ts,
            event=event,
            model=model,
            worker=worker,
            owner=owner,
            backend=backend,
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            token_rate=token_rate,
            reason=reason,
            detail=dict(detail or {}),
        )
        self._ring.append(ev)
        self._fanout(ev)
        return ev

    def _fanout(self, ev: ModelActivityEvent) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                # Drop the oldest queued event rather than dropping this one:
                # a slow reader sees the newest activity, not a frozen view.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race only
                    pass
            try:
                queue.put_nowait(ev)
            except asyncio.QueueFull:  # pragma: no cover - emptied above
                logger.debug("model_activity: dropping event for a full subscriber queue")

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def snapshot(
        self,
        *,
        limit: int = 100,
        model: str | None = None,
        worker: str | None = None,
        event: str | None = None,
        owner: str | None = None,
    ) -> list[ModelActivityEvent]:
        """Return matching events, newest first, capped at *limit*.

        Filters are exact-match on ``model`` / ``worker`` / ``event`` /
        ``owner``; every filter defaults to "no filter", which is what the
        admin-scoped read path wants. A caller that must not see another
        principal's events passes its own ``owner``.
        """
        if limit <= 0:
            return []
        out: list[ModelActivityEvent] = []
        for ev in reversed(self._ring):
            if model is not None and ev.model != model:
                continue
            if worker is not None and ev.worker != worker:
                continue
            if event is not None and ev.event != event:
                continue
            if owner is not None and ev.owner != owner:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[ModelActivityEvent]:
        """Register a live queue.  The topic has no replay: callers seed from
        :meth:`snapshot` and then consume this queue for live events."""
        queue: asyncio.Queue[ModelActivityEvent] = asyncio.Queue(
            maxsize=self._subscriber_maxlen
        )
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ModelActivityEvent]) -> None:
        self._subscribers.discard(queue)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def maxlen(self) -> int:
        return self._ring.maxlen or 0

    @property
    def last_seq(self) -> int:
        """Sequence of the most recent event, or 0 when nothing was recorded.

        The counter is per-process: it restarts at 1 on a controller restart.
        Consumers holding a resume id from a previous process must compare it
        against this before trusting it.
        """
        return self._seq

    def __len__(self) -> int:
        return len(self._ring)

    def stats(self) -> dict[str, int]:
        return {
            "size": len(self._ring),
            "capacity": self.maxlen,
            "subscribers": len(self._subscribers),
            "recorded": self._seq,
        }