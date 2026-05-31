"""Tests for IdempotencyCache — concurrency-safe request deduplication."""
import asyncio

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
