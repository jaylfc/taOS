"""Tests for IdempotencyCache — concurrency-safe request deduplication."""
import asyncio
import time

import pytest

from tinyagentos.routes.agents import IdempotencyCache


@pytest.mark.asyncio
class TestIdempotencyCache:
    async def test_try_reserve_returns_proceed_on_first_call(self):
        """First caller gets ('proceed', event) — owns the key."""
        cache = IdempotencyCache()
        mode, event = cache.try_reserve("req-1")
        assert mode == "proceed"
        assert isinstance(event, asyncio.Event)
        assert not event.is_set()

    async def test_try_reserve_returns_wait_on_second_call(self):
        """Second caller with the same key gets ('wait', event)
        and receives the SAME event object the first caller holds."""
        cache = IdempotencyCache()
        _, event1 = cache.try_reserve("req-1")
        mode2, event2 = cache.try_reserve("req-1")
        assert mode2 == "wait"
        assert event2 is event1  # Same sentinel — wait on the same future

    async def test_set_fires_event_waking_waiters(self):
        """set() fires the event so all waiters can proceed."""
        cache = IdempotencyCache()
        _, event = cache.try_reserve("req-1")
        # Second caller would get 'wait' with this same event
        _, waiter_event = cache.try_reserve("req-1")
        assert not event.is_set()

        cache.set("req-1", {"status": "created"})
        assert event.is_set()
        # The waiter can now get() the result
        assert cache.get("req-1") == {"status": "created"}

    async def test_retry_after_completion_finds_cached_result(self):
        """After set() stores a result, a new try_reserve on the
        same key returns 'wait' with an already-set event, and
        get() returns the cached result."""
        cache = IdempotencyCache()
        cache.try_reserve("req-1")
        cache.set("req-1", {"status": "created", "name": "agent-x"})

        # "Retry" — another request with the same Idempotency-Key
        mode, event = cache.try_reserve("req-1")
        assert mode == "wait"
        assert event.is_set()  # Already resolved — no need to actually await

        result = cache.get("req-1")
        assert result == {"status": "created", "name": "agent-x"}

    async def test_different_keys_do_not_interfere(self):
        """Each idempotency key is independently tracked."""
        cache = IdempotencyCache()
        mode_a, event_a = cache.try_reserve("key-a")
        mode_b, event_b = cache.try_reserve("key-b")
        assert mode_a == "proceed"
        assert mode_b == "proceed"
        assert event_a is not event_b

        cache.set("key-a", {"result": "a"})
        assert event_a.is_set()
        assert not event_b.is_set()
        assert cache.get("key-a") == {"result": "a"}
        assert cache.get("key-b") is None

    async def test_get_returns_none_for_unknown_key(self):
        """get() returns None when the key was never reserved."""
        cache = IdempotencyCache()
        assert cache.get("never-seen") is None

    async def test_set_on_unreserved_key_stores_result(self):
        """Calling set() without a prior try_reserve() still
        stores the result for get()."""
        cache = IdempotencyCache()
        cache.set("direct-set", {"status": "ok"})
        assert cache.get("direct-set") == {"status": "ok"}

    async def test_multiple_waiters_all_see_same_result(self):
        """When multiple callers reserve the same key, set()
        wakes all of them and they see the same cached result."""
        cache = IdempotencyCache()
        cache.try_reserve("req-1")       # first — proceed
        cache.try_reserve("req-1")       # second — wait
        cache.try_reserve("req-1")       # third — wait

        cache.set("req-1", {"status": "deployed"})
        # All subsequent retrievals return the same result
        assert cache.get("req-1") == {"status": "deployed"}
        assert cache.get("req-1") == {"status": "deployed"}


class TestIdempotencyCacheEviction:
    """LRU eviction and TTL expiry tests."""

    def test_lru_eviction_past_max_size(self):
        """When the cache exceeds _MAX_SIZE completed entries, the oldest
        completed entry is evicted so size stays bounded."""
        cache = IdempotencyCache()
        # Lower the cap so the test doesn't need to insert 1000 items.
        cache._MAX_SIZE = 5

        # Fill with 5 completed entries.
        for i in range(5):
            key = f"key-{i}"
            cache.try_reserve(key)
            cache.set(key, {"n": i})

        # All 5 are present.
        assert len(cache._entries) == 5

        # Adding a 6th triggers eviction of the oldest (key-0).
        cache.try_reserve("key-new")
        cache.set("key-new", {"n": 99})

        assert len(cache._entries) == 5
        assert cache.get("key-0") is None          # evicted
        assert cache.get("key-new") == {"n": 99}   # newest present

    def test_lru_does_not_evict_in_flight_entries(self):
        """In-flight entries (event not yet set) must not be evicted even
        when the cache is at capacity — evicting them would strand waiters."""
        cache = IdempotencyCache()
        cache._MAX_SIZE = 3

        # Reserve 3 keys but don't complete them (in-flight).
        for i in range(3):
            cache.try_reserve(f"inflight-{i}")

        # Trying to add a 4th should NOT evict any in-flight entry because
        # _evict_if_needed skips entries whose event is not set.
        cache.try_reserve("new-key")

        # All 4 entries still present (no safe eviction target).
        assert len(cache._entries) == 4

    def test_ttl_expiry_on_get_returns_none(self):
        """get() returns None and removes the entry once TTL has elapsed."""
        cache = IdempotencyCache()
        cache.try_reserve("old-key")
        cache.set("old-key", {"status": "created"})

        # Back-date the insertion timestamp to simulate TTL expiry.
        ev, res, _ts = cache._entries["old-key"]
        cache._entries["old-key"] = (ev, res, time.monotonic() - cache._TTL_SECONDS - 1)

        assert cache.get("old-key") is None
        assert "old-key" not in cache._entries

    def test_ttl_expiry_on_try_reserve_allows_fresh_reserve(self):
        """try_reserve on a TTL-expired key returns 'proceed' and issues a
        new event, allowing the request to be processed again."""
        cache = IdempotencyCache()
        cache.try_reserve("stale")
        cache.set("stale", {"status": "created"})

        # Expire the entry.
        ev, res, _ts = cache._entries["stale"]
        cache._entries["stale"] = (ev, res, time.monotonic() - cache._TTL_SECONDS - 1)

        mode, new_event = cache.try_reserve("stale")
        assert mode == "proceed"
        assert not new_event.is_set()

    def test_non_expired_entry_not_evicted_by_ttl(self):
        """Fresh entries survive TTL checks and remain accessible."""
        cache = IdempotencyCache()
        cache.try_reserve("fresh")
        cache.set("fresh", {"status": "ok"})

        # Should still be present immediately after set().
        assert cache.get("fresh") == {"status": "ok"}

    def test_max_size_constant_is_sensible(self):
        """Sanity-check: default _MAX_SIZE is 1000 and _TTL_SECONDS is 3600."""
        cache = IdempotencyCache()
        assert cache._MAX_SIZE == 1000
        assert cache._TTL_SECONDS == 3600.0
