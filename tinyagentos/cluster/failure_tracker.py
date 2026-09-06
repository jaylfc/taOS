"""Circuit breaker for cluster worker routing.

Tracks per-worker failure counts within a sliding time window so the
TaskRouter can skip workers that have repeatedly failed, instead of
serially trying all N workers and blocking for ``timeout * N`` seconds
before giving up (taOS #640, Fix 3).

State is in-memory only (no SQLite): the circuit breaker resets on
controller restart, which is the safe default — a freshly-started
controller has no failure history and should give every worker a
clean chance.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


# Sensible defaults: 3 failures in 60 seconds trips the breaker.
# A tripped worker is excluded from routing for the cooldown period.
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_WINDOW_SECONDS = 60.0


class FailureTracker:
    """Per-worker failure counter with a true sliding time window.

    After ``failure_threshold`` failures within ``window_seconds``,
    ``is_tripped(worker_name)`` returns ``True`` and the router skips
    that worker.  The window slides continuously — only failures that
    occurred within the last ``window_seconds`` are counted, regardless
    of when the first failure in any prior window happened.
    """

    def __init__(
        self,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ):
        self._failure_threshold = failure_threshold
        self._window_seconds = window_seconds
        # Each worker maps to a deque of failure timestamps (monotonic, oldest-first).
        self._records: dict[str, deque[float]] = defaultdict(deque)
        # Injectable for testing — set in subclasses or via monkeypatch.
        self._time_func = time.time

    # ── public API ──────────────────────────────────────────────────────

    def record_failure(self, worker_name: str) -> None:
        """Register a failure for the given worker at the current time."""
        now = self._time_func()
        q = self._records[worker_name]
        q.append(now)

    def record_success(self, worker_name: str) -> None:
        """Clear the failure record for a worker after a successful request."""
        self._records.pop(worker_name, None)

    def is_tripped(self, worker_name: str) -> bool:
        """Return True if the worker's circuit breaker is currently open.

        The breaker opens when ``failure_threshold`` failures have occurred
        within the last ``window_seconds``.  Stale timestamps are pruned
        automatically before the check.
        """
        q = self._records.get(worker_name)
        if q is None:
            return False
        now = self._time_func()
        self._prune(worker_name, now)
        # Re-check after pruning — _prune may have cleared the queue entirely.
        q = self._records.get(worker_name)
        if q is None:
            return False
        return len(q) >= self._failure_threshold

    def reset(self, worker_name: str) -> None:
        """Manually reset the circuit breaker for a worker."""
        self._records.pop(worker_name, None)

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        self._records.clear()

    # ── internal helpers ────────────────────────────────────────────────

    def _prune(self, worker_name: str, now: float) -> None:
        """Remove timestamps older than ``now - window_seconds``."""
        q = self._records.get(worker_name)
        if q is None:
            return
        cutoff = now - self._window_seconds
        # Deque is oldest-first — popleft until we hit a recent timestamp.
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._records[worker_name]
