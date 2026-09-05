"""Tests for tinyagentos.rate_limit."""
from __future__ import annotations

import time

from tinyagentos import auth_middleware
import pytest

from tinyagentos.rate_limit import (
    MovingWindowLimiter,
    RateLimiter,
    TokenBucket,
    make_should_rate_limit,
)


class TestTokenBucket:
    def test_starts_full(self):
        b = TokenBucket(capacity=5, refill_per_second=1)
        for _ in range(5):
            assert b.try_consume() is True
        assert b.try_consume() is False

    def test_refills_over_time(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])
        b = TokenBucket(capacity=5, refill_per_second=2)
        for _ in range(5):
            assert b.try_consume() is True
        assert b.try_consume() is False
        now[0] += 1.5  # 3 tokens should refill
        assert b.try_consume() is True
        assert b.try_consume() is True
        assert b.try_consume() is True
        assert b.try_consume() is False

    def test_capacity_cap(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])
        b = TokenBucket(capacity=5, refill_per_second=10)
        b.try_consume()  # 4 left
        now[0] += 100  # lots of time, but cap at 5
        assert b.try_consume() is True
        for _ in range(4):
            assert b.try_consume() is True
        assert b.try_consume() is False


class TestRateLimiter:
    def test_separate_keys(self):
        r = RateLimiter(capacity=2, refill_per_second=0)
        assert r.check("ip-a") is True
        assert r.check("ip-b") is True
        assert r.check("ip-a") is True
        assert r.check("ip-a") is False
        assert r.check("ip-b") is True
        assert r.check("ip-b") is False


class TestShouldRateLimit:
    def test_get_exempt(self):
        pred = make_should_rate_limit(["/api/agents/"])
        assert pred("GET", "/api/agents/foo") is False

    def test_post_matching_prefix(self):
        pred = make_should_rate_limit(["/api/agents/"])
        assert pred("POST", "/api/agents/foo/chat") is True

    def test_post_non_matching(self):
        pred = make_should_rate_limit(["/api/agents/"])
        assert pred("POST", "/api/cluster/workers") is False

    def test_multiple_prefixes(self):
        pred = make_should_rate_limit(["/api/agents/", "/api/cluster/route"])
        assert pred("POST", "/api/cluster/route") is True
        assert pred("POST", "/api/cluster/workers") is False


class TestBoundedMemory:
    """No limiter registry may grow without bound.

    The key is the client IP and some of the endpoints behind these limiters
    are unauthenticated, so an attacker with an IPv6 /64 has 2^64 distinct
    keys to spend. On a 4 GB board an unbounded registry is a reachable
    memory-exhaustion DoS, so every registry evicts once it is full.
    """

    def test_token_bucket_registry_is_bounded(self):
        r = RateLimiter(capacity=2, refill_per_second=1.0)
        for i in range(50_000):
            r.check(f"2001:db8::{i:x}")
        assert len(r._buckets) <= 2000, (
            f"expected len(_buckets) <= 2000, got {len(r._buckets)}"
        )

    def test_invite_window_registry_is_bounded(self):
        auth_middleware._rate_limit_hits.clear()
        try:
            for i in range(50_000):
                auth_middleware.rate_limit_ok(f"2001:db8::{i:x}")
            tracked = len(auth_middleware._rate_limit_hits)
            assert tracked <= 2000, (
                f"expected len(_rate_limit_hits) <= 2000, got {tracked}"
            )
        finally:
            auth_middleware._rate_limit_hits.clear()


class TestWindowBoundary:
    """The documented cap must hold over *any* span of one window.

    A fixed window that resets its counter on the first request after the
    window elapsed lets a caller land the full allowance just before the
    boundary and the full allowance again just after — twice the documented
    burst inside a fraction of a window. On an endpoint whose proof of
    possession is an 8-digit PIN that halves the brute-force cost.
    """

    @staticmethod
    def _freeze(monkeypatch, clock):
        """Pin both clocks: the limiter may read either one."""
        monkeypatch.setattr(time, "monotonic", lambda: clock["mono"])
        monkeypatch.setattr(time, "time", lambda: clock["wall"])

    def test_no_double_burst_across_the_window_edge(self, monkeypatch):
        clock = {"mono": 1000.0, "wall": 1000.0}
        self._freeze(monkeypatch, clock)
        auth_middleware._rate_limit_hits.clear()
        try:
            # One request opens the window at t=0.
            assert auth_middleware.rate_limit_ok("ip") is True

            accepted = 0
            clock["mono"] = clock["wall"] = 1009.9
            for _ in range(19):
                accepted += bool(auth_middleware.rate_limit_ok("ip"))
            clock["mono"] = clock["wall"] = 1010.1
            for _ in range(20):
                accepted += bool(auth_middleware.rate_limit_ok("ip"))

            assert accepted <= 20, (
                "expected <= 20 requests accepted across t=9.9s..t=10.1s, "
                f"got {accepted}"
            )
        finally:
            auth_middleware._rate_limit_hits.clear()

    def test_backward_wall_clock_step_does_not_freeze_the_window(self, monkeypatch):
        # An NTP correction steps the wall clock backwards — routine on an
        # RTC-less board after a cold boot. A limiter that measures elapsed
        # time with time.time() then sees a negative delta, never reopens its
        # window, and locks the caller out until the clock catches up.
        clock = {"mono": 500.0, "wall": 1000.0}
        self._freeze(monkeypatch, clock)
        auth_middleware._rate_limit_hits.clear()
        try:
            for _ in range(20):
                assert auth_middleware.rate_limit_ok("ip") is True
            assert auth_middleware.rate_limit_ok("ip") is False

            clock["wall"] -= 3600.0  # NTP steps back an hour
            clock["mono"] += 11.0  # 11 real seconds pass; the window is over
            assert auth_middleware.rate_limit_ok("ip") is True, (
                "window frozen by a backward wall-clock step"
            )
        finally:
            auth_middleware._rate_limit_hits.clear()


class TestMaxKeysValidation:
    """max_keys <= 0 must be rejected up front, not surfaced as a 500.

    With max_keys <= 0 the first never-seen key enters the eviction loop
    against an empty registry (`len(registry) >= max_keys` is already true),
    and `popitem()` on an empty OrderedDict/dict raises KeyError -- a
    request-time 500 instead of a clean construction-time error.
    """

    def test_rate_limiter_rejects_zero_max_keys(self):
        with pytest.raises(ValueError):
            RateLimiter(capacity=2, refill_per_second=1.0, max_keys=0)

    def test_rate_limiter_rejects_negative_max_keys(self):
        with pytest.raises(ValueError):
            RateLimiter(capacity=2, refill_per_second=1.0, max_keys=-1)

    def test_moving_window_limiter_rejects_zero_max_keys(self):
        with pytest.raises(ValueError):
            MovingWindowLimiter(max_per_window=5, window_secs=60, max_keys=0)

    def test_moving_window_limiter_rejects_negative_max_keys(self):
        with pytest.raises(ValueError):
            MovingWindowLimiter(max_per_window=5, window_secs=60, max_keys=-1)
