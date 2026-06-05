from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class SystemEvent:
    kind: str
    source: str  # e.g. "system", "scheduler", or an agent name
    targets: list[str]  # agent ids and/or sentinels "user" / "broadcast"
    payload: dict[str, Any]
    level: str = "info"
    ts: float = field(default_factory=time.time)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))


# Sentinel channel that every subscriber can also listen on for all events.
_BROADCAST_CHANNEL = "broadcast"

PermissionCheck = Callable[[SystemEvent], bool | Awaitable[bool]]


class EventBus:
    """In-process pub/sub for SystemEvents, mirroring ProjectEventBroker.

    Channels correspond to target names (agent ids) plus the "broadcast"
    sentinel that receives every emitted event regardless of targets.

    Single-worker assumption: all subscribers/publishers share one process.
    """

    def __init__(self, replay_size: int = 32) -> None:
        self._replay_size = replay_size
        self._queues: dict[str, list[asyncio.Queue[SystemEvent]]] = {}
        self._replay: dict[str, deque[SystemEvent]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe / replay  (mirrors ProjectEventBroker API)
    # ------------------------------------------------------------------

    async def subscribe(self, channel: str) -> asyncio.Queue[SystemEvent]:
        """Return a queue that receives future events on *channel*.

        Already-buffered events are replayed into the queue immediately.
        """
        queue: asyncio.Queue[SystemEvent] = asyncio.Queue()
        async with self._lock:
            self._queues.setdefault(channel, []).append(queue)
            for ev in self._replay.get(channel, ()):
                queue.put_nowait(ev)
        return queue

    async def unsubscribe(self, channel: str, queue: asyncio.Queue[SystemEvent]) -> None:
        async with self._lock:
            qs = self._queues.get(channel, [])
            if queue in qs:
                qs.remove(queue)

    async def _publish_to_channel(self, channel: str, event: SystemEvent) -> None:
        """Append *event* to the replay buffer and fan it out to all queues."""
        buf = self._replay.setdefault(channel, deque(maxlen=self._replay_size))
        buf.append(event)
        for q in list(self._queues.get(channel, [])):
            q.put_nowait(event)

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    async def emit(
        self,
        event: SystemEvent,
        *,
        notifications,
        agent_messages,
        trace_store,
        permission_check: PermissionCheck | None = None,
    ) -> None:
        """Route *event* through all sinks.

        Steps:
        a. Run permission_check if provided; drop silently on False.
        b. Persist to trace_store.
        c. If "user" in targets or level in {warning, error}: push a
           user notification.
        d. For each agent id in targets: send an agent message.
        e. Publish to in-process channels for each target + "broadcast".
        """
        # a. Permission gate (stub; default allow)
        if permission_check is not None:
            result = permission_check(event)
            if asyncio.iscoroutine(result):
                result = await result
            if not result:
                return

        # b. Persist to trace store
        try:
            await trace_store.add(event)
        except Exception:
            logger.exception("EventBus: trace_store.add failed for event %s", event.trace_id)

        # c. User notification
        should_notify_user = (
            "user" in event.targets
            or event.level in {"warning", "error"}
        )
        if should_notify_user:
            title, message = _derive_notification(event)
            try:
                await notifications.add(title, message, level=event.level, source=event.source)
            except Exception:
                logger.exception("EventBus: notifications.add failed for event %s", event.trace_id)

        # d. Agent messages — deduplicate targets to avoid duplicate sends
        _seen_targets: dict[str, None] = dict.fromkeys(event.targets)
        for target in _seen_targets:
            if target in ("user", _BROADCAST_CHANNEL):
                continue
            try:
                await agent_messages.send(
                    from_agent="system",
                    to_agent=target,
                    message=json.dumps({"kind": event.kind, "payload": event.payload,
                                        "trace_id": event.trace_id, "ts": event.ts}),
                )
            except Exception:
                logger.exception(
                    "EventBus: agent_messages.send failed for target=%s event=%s",
                    target, event.trace_id,
                )

        # e. In-process pub/sub — deduplicate, then ensure broadcast is published
        #    exactly once even if it appeared in targets.
        async with self._lock:
            for target in _seen_targets:
                await self._publish_to_channel(target, event)
            if _BROADCAST_CHANNEL not in _seen_targets:
                await self._publish_to_channel(_BROADCAST_CHANNEL, event)


# ------------------------------------------------------------------
# Notification text derivation
# ------------------------------------------------------------------

def _derive_notification(event: SystemEvent) -> tuple[str, str]:
    """Return (title, message) for a user-visible notification."""
    kind_label = event.kind.replace(".", " ").replace("_", " ").title()
    title = f"{kind_label}"
    # Use payload "message" key if present, otherwise a short summary
    message = event.payload.get("message") or event.payload.get("detail") or ""
    if not message:
        message = f"source={event.source}"
    return title, message


# ------------------------------------------------------------------
# App-state helper
# ------------------------------------------------------------------

async def emit_event(app_state, event: SystemEvent) -> None:
    """Convenience helper: pull stores from app_state and call bus.emit.

    Usage in a route or background task::

        from tinyagentos.events import emit_event, SystemEvent
        await emit_event(request.app.state, SystemEvent(
            kind="worker.join", source="cluster", targets=["user"],
            payload={"worker_id": w_id},
        ))
    """
    bus: EventBus = app_state.event_bus
    await bus.emit(
        event,
        notifications=app_state.notifications,
        agent_messages=app_state.agent_messages,
        trace_store=app_state.system_events,
    )
