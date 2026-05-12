"""Idempotency-Key support for POST endpoints that create durable resources.

If a request carries an `Idempotency-Key` header, the (key, endpoint, user_id)
tuple is cached for 24h. A repeat request with the same key and endpoint
returns the cached response — useful when an agent retries a deploy after a
network blip and shouldn't end up with two agents.

The cache lives on `app.state.idempotency_cache` so each app instance gets
its own (tests are isolated; production gets one shared cache per worker).
In-memory only; persistence across restarts is a Pass 2+ concern.

Bounded LRU: the cache holds at most `_MAX_ENTRIES` items. When full, the
oldest insertion is evicted regardless of its remaining TTL. This caps
memory growth from high-cardinality keys (random UUID per call).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

_TTL_SECONDS = 24 * 3600
_MAX_ENTRIES = 1024  # ~1k inflight idempotency keys per worker is enough


class IdempotencyCache:
    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._entries: OrderedDict[tuple[str, str, str], tuple[Any, float]] = OrderedDict()
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    async def get(self, *, key: str, endpoint: str, user_id: str) -> Any | None:
        async with self._lock:
            k = (key, endpoint, user_id)
            entry = self._entries.get(k)
            if entry is None:
                return None
            value, ts = entry
            if time.time() - ts > _TTL_SECONDS:
                self._entries.pop(k, None)
                return None
            # Mark recently used so LRU eviction skips it.
            self._entries.move_to_end(k)
            return value

    async def set(self, *, key: str, endpoint: str, user_id: str, value: Any) -> None:
        async with self._lock:
            k = (key, endpoint, user_id)
            self._entries[k] = (value, time.time())
            self._entries.move_to_end(k)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
