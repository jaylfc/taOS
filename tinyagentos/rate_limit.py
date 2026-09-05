"""In-process rate limiters for controller endpoints.

This is a defence-in-depth knob, not a full DDoS shield. It exists so
that a runaway agent loop, a misconfigured MCP client, or an accidental
infinite retry cannot hammer the controller into exhaustion. Two shapes
are offered and every limiter in the controller uses one of them:

``RateLimiter``
    A token bucket per key: bursts up to the capacity are allowed, then
    requests over the steady rate get a ``429 Too Many Requests``.
``MovingWindowLimiter``
    "At most N requests in any span of W seconds" per key, for the
    endpoints whose limit is documented that way (peer traffic, cluster
    pairing claims, project-invite redeems).

Both are keyed on whatever granularity the caller picks — usually the
client IP, sometimes an authenticated user or contact id — and both cap
how many keys they track (``MAX_TRACKED_KEYS``). That cap is not
housekeeping: the unauthenticated endpoints are keyed by IP, and an
attacker with an IPv6 /64 has 2^64 distinct keys to spend, so an
unbounded registry is a reachable memory-exhaustion DoS on a small
board.

Both read ``time.monotonic()``. Wall-clock time is not usable here: an
NTP step backwards, routine on an RTC-less board after a cold boot,
makes an elapsed-time check negative and freezes the window.

The controller wires ``RateLimiter`` in as a FastAPI middleware for
mutating endpoints only — read-only GETs (health, cluster list,
dashboards) are exempt so a UI refresh storm does not start 429-ing.
Limiter state is in-process; a restart resets everything, which is the
right trade-off for a self-hosted single-process controller.

Users who need cross-process or cross-host rate limiting should front
the controller with Caddy or nginx and use their built-in limiters.
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable

from fastapi.responses import JSONResponse

# Maximum distinct keys a limiter tracks before it starts evicting. 2000
# concurrent callers is far beyond anything a self-hosted controller sees,
# while the memory is trivial (a few hundred KB worst case).
MAX_TRACKED_KEYS = 2000


@dataclass
class TokenBucket:
    """A classic token bucket.

    ``capacity`` is the maximum burst size. ``refill_per_second`` is
    the steady-state rate. ``tokens`` is the current fill; when a
    request arrives we refill based on elapsed time, then try to
    consume one token.
    """

    capacity: float
    refill_per_second: float
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.last_refill = time.monotonic()

    def tokens_at(self, now: float) -> float:
        """Fill level at ``now``, without consuming or mutating anything."""
        elapsed = now - self.last_refill
        return min(self.capacity, self.tokens + elapsed * self.refill_per_second)

    def try_consume(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = self.tokens_at(now)
        self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def seconds_until(self, cost: float = 1.0) -> float:
        """Seconds until ``cost`` tokens are available; 0 when they are now.

        ``inf`` for a bucket that never refills — there is no honest answer
        to "when should I retry?" for one of those.
        """
        tokens = self.tokens_at(time.monotonic())
        if tokens >= cost:
            return 0.0
        if self.refill_per_second <= 0:
            return math.inf
        return (cost - tokens) / self.refill_per_second


class RateLimiter:
    """Per-key bucket registry, bounded at ``max_keys`` entries.

    The key is typically the client IP, but the caller picks whatever
    granularity it wants. A dedicated key per authenticated user, a
    shared key for all anonymous traffic, whatever.
    """

    def __init__(self, capacity: float = 30, refill_per_second: float = 10.0,
                 *, max_keys: int = MAX_TRACKED_KEYS):
        if max_keys < 1:
            raise ValueError("max_keys must be at least 1")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.max_keys = max_keys
        # Ordered by least-recently-used so eviction is O(1).
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str, cost: float = 1.0) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._evict_if_full()
                bucket = self._buckets[key] = TokenBucket(
                    self.capacity, self.refill_per_second
                )
            else:
                self._buckets.move_to_end(key)
            return bucket.try_consume(cost)

    def retry_after(self, key: str, cost: float = 1.0) -> int:
        """Whole seconds to advertise in ``Retry-After``; at least 1."""
        with self._lock:
            bucket = self._buckets.get(key)
            wait = bucket.seconds_until(cost) if bucket is not None else 0.0
        if math.isinf(wait):
            # Only a never-refilling bucket gets here (test configuration);
            # advertise the shortest honest-ish backoff rather than a lie.
            return 1
        return max(1, math.ceil(wait))

    def clear(self) -> None:
        """Forget every bucket. For tests and for an operator reset."""
        with self._lock:
            self._buckets.clear()

    def _evict_if_full(self) -> None:
        """Make room for one new key. Caller holds the lock.

        The least recently used bucket goes. A bucket that has been idle
        has refilled to capacity, so dropping it is free — recreating it
        yields exactly the same full bucket. Under a flood large enough
        that every tracked bucket is still drained, eviction hands one key
        a free reset, which is strictly better than exhausting memory.
        """
        while len(self._buckets) >= self.max_keys:
            self._buckets.popitem(last=False)


class MovingWindowLimiter:
    """Per-key moving-window limiter, bounded at ``max_keys`` entries.

    "At most ``max_per_window`` requests in any span of ``window_secs``".
    Deliberately *moving*, not fixed: a fixed window resets its counter on
    the first request after the window elapses, so a caller can land the
    full allowance just before the boundary and the full allowance again
    just after — twice the documented burst inside a fraction of a window.
    On an endpoint whose proof of possession is an 8-digit numeric PIN,
    that halves the brute-force cost.

    ``hits`` maps key -> the timestamps still inside its window, oldest
    first. It is public so the module that owns a limiter can alias it and
    let tests and operators reset a window.

    Per-process only: under a multi-worker deployment each worker keeps its
    own counters, so the effective aggregate limit is
    ``max_per_window x N``. Front the controller with Caddy or nginx when
    an exact cross-process limit matters.
    """

    def __init__(self, max_per_window: int, window_secs: float,
                 *, max_keys: int = MAX_TRACKED_KEYS):
        if max_keys < 1:
            raise ValueError("max_keys must be at least 1")
        self.max_per_window = max_per_window
        self.window_secs = float(window_secs)
        self.max_keys = max_keys
        # Ordered by least-recently-used so eviction is O(1).
        self.hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """Record a request for ``key``; False when it is over the limit.

        A rejected request is not recorded, so a caller that keeps hammering
        cannot push its own window out indefinitely.
        """
        with self._lock:
            now = time.monotonic()
            window = self.hits.get(key)
            if window is None:
                while len(self.hits) >= self.max_keys:
                    # Least recently used: its window is the closest to
                    # expiry, so this is the cheapest key to forget.
                    self.hits.popitem(last=False)
                window = self.hits[key] = deque()
            else:
                self.hits.move_to_end(key)
            self._expire(window, now)
            if len(window) >= self.max_per_window:
                return False
            window.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Whole seconds to advertise in ``Retry-After``; at least 1."""
        with self._lock:
            now = time.monotonic()
            window = self.hits.get(key)
            if window is None or len(window) < self.max_per_window:
                return 1
            # The oldest hit leaving the window frees the next slot.
            return max(1, math.ceil(window[0] + self.window_secs - now))

    def clear(self) -> None:
        """Forget every window. For tests and for an operator reset."""
        with self._lock:
            self.hits.clear()

    def _expire(self, window: deque[float], now: float) -> None:
        cutoff = now - self.window_secs
        while window and window[0] <= cutoff:
            window.popleft()


def retry_after_headers(seconds: int) -> dict[str, str]:
    """``Retry-After`` header for a 429, as whole seconds (RFC 9110 §10.2.3)."""
    return {"Retry-After": str(max(1, int(seconds)))}


def rate_limited_response(message: str, retry_after: int) -> JSONResponse:
    """The controller's standard 429: an ``error`` body plus ``Retry-After``.

    Without the header a well-behaved client has nothing to back off on and
    retries as fast as it can fail.
    """
    return JSONResponse(
        {"error": message},
        status_code=429,
        headers=retry_after_headers(retry_after),
    )


def make_should_rate_limit(paths_to_limit: list[str]) -> Callable[[str, str], bool]:
    """Build a path-matching predicate.

    ``paths_to_limit`` is a list of path prefixes that *should* be rate
    limited. Returns a callable ``(method, path) -> bool`` that answers
    "should this request be gated on the limiter?" The controller's
    middleware calls it to decide whether to check the bucket at all.
    GET requests are always exempt, since reading controller state is
    cheap and a UI refresh loop should not be penalised.
    """

    def should(method: str, path: str) -> bool:
        if method.upper() == "GET":
            return False
        return any(path.startswith(p) for p in paths_to_limit)

    return should
