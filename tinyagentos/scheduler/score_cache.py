"""Score cache, sync-friendly view of the async benchmark store.

The scheduler's admission path has to be synchronous (``Resource.can_admit``
is called from within an async Task dispatch, but sync checks avoid
complex nesting and make tests readable). The benchmark store is async.

This module bridges the gap: a tiny in-memory cache populated by a
background polling task that reads the latest benchmark row per
(worker, capability, model) tuple. ``Resource.score_for()`` gets a
sync callable that reads from the cache, no awaits, no locks, fast.

Resolution:
- worker_id matches the Resource name on the local machine
  (``npu-rk3588``, ``cpu-inference``, ``gpu-cuda-0``...)
- cluster workers match their heartbeat-reported name
- When the same (capability, model) pair has rows from multiple workers
  the cache stores the best score per (resource, capability, model)

Cache is updated every ~15 s. Stale entries are still returned, the
scheduler prefers stale data to no data. Real freshness management is
Phase 2.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class ScoreCache:
    """In-memory cache of latest benchmark scores per (resource, capability).

    The cache key is ``(resource_name, capability)`` because the scheduler
    asks ``resource.score_for(capability)`` without knowing the model id
    (it's operating at the capability level). For resources that can serve
    the same capability with different models, we take the best recent
    score across them, the scheduler picks the resource, the resource
    picks the specific backend/model at execution time.
    """

    def __init__(self, benchmark_store, poll_interval_seconds: float = 15.0):
        self._store = benchmark_store
        self._interval = poll_interval_seconds
        self._cache: dict[tuple[str, str], float] = {}
        self._updated_at = 0.0
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task is not None:
            return
        # Seed the cache synchronously so callers get real data on the
        # first admission instead of None.
        try:
            await self._refresh()
        except Exception:
            logger.exception("initial score cache refresh failed")
        self._task = asyncio.create_task(self._poll_loop(), name="score-cache-poll")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def score(self, resource_name: str, capability: str) -> Optional[float]:
        """Sync score lookup. Returns None if nothing is cached yet."""
        return self._cache.get((resource_name, capability))

    def snapshot(self) -> dict:
        return {
            "updated_at": self._updated_at,
            "scores": {
                f"{res}:{cap}": val for (res, cap), val in self._cache.items()
            },
        }

    async def _poll_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self._refresh()
                except Exception:
                    logger.exception("score cache refresh failed")
        except asyncio.CancelledError:
            raise

    async def _refresh(self) -> None:
        """Walk a small set of capabilities and pull the leaderboard for each.

        Uses the existing ``leaderboard()`` query on the store, which returns
        latest-per-worker for a capability. We read the best row per
        (worker_id, metric) and store it as the canonical score for that
        (resource, capability) key.
        """
        if self._store is None:
            return
        capabilities = (
            "embedding",
            "reranking",
            "llm-chat",
            "image-generation",
            "speech-to-text",
            "text-to-speech",
            "vision",
        )
        new_cache: dict[tuple[str, str], float] = {}
        for cap in capabilities:
            try:
                rows = await self._store.leaderboard(cap)
            except Exception:
                continue
            for row in rows:
                worker = row.get("worker_id")
                value = row.get("value")
                if not worker or value is None:
                    continue
                key = (worker, cap)
                # Keep the best-per-(resource, capability). For latency
                # metrics this is wrong (lower is better), Phase 2 adds
                # metric-aware ranking. For Phase 1 we use raw value and
                # trust throughput-style metrics dominate the default suite.
                existing = new_cache.get(key)
                if existing is None or value > existing:
                    new_cache[key] = float(value)
        self._cache = new_cache
        self._updated_at = time.time()
