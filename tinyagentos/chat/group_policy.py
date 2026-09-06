"""Per-channel per-agent cooldown + channel-wide rate cap.

In-memory, single-process (matches current taOS router architecture).
Thread-safe enough for asyncio single-threaded use.
"""
from __future__ import annotations

import time
from collections import deque


def _now() -> float:
    return time.monotonic()


class GroupPolicy:
    def __init__(self) -> None:
        self._last_send_at: dict[tuple[str, str], float] = {}
        self._recent_sends: dict[str, deque[float]] = {}

    def may_send(self, channel_id: str, agent: str, settings: dict) -> bool:
        now = _now()
        cooldown = int(settings.get("cooldown_seconds", 5))
        cap = int(settings.get("rate_cap_per_minute", 20))

        last = self._last_send_at.get((channel_id, agent))
        if last is not None and (now - last) < cooldown:
            return False

        window = self._recent_sends.get(channel_id)
        if window:
            while window and (now - window[0]) > 60.0:
                window.popleft()
            if len(window) >= cap:
                return False
        return True

    def record_send(self, channel_id: str, agent: str) -> None:
        now = _now()
        self._last_send_at[(channel_id, agent)] = now
        window = self._recent_sends.setdefault(channel_id, deque(maxlen=256))
        window.append(now)

    def try_acquire(self, channel_id: str, agent: str, settings: dict) -> bool:
        """Atomically: check if sending is allowed and, if so, record it.
        Returns True iff the send is permitted; in that case the send has
        already been recorded. No await between check and record means
        concurrent asyncio callers cannot both pass when only one should.
        """
        if not self.may_send(channel_id, agent, settings):
            return False
        self.record_send(channel_id, agent)
        return True
