"""Unit tests for the benchmark store.

Covers the invariants the 'first-join runs exactly once' policy relies on:
- has_first_join_run() returns False until a first_join row is inserted
- After insertion, a manual rerun can be recorded without polluting the
  first_join flag
- latest_by_worker returns one row per (capability, model) pair
- leaderboard ranks workers by value for a given capability + metric
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from tinyagentos.benchmark.store import BenchmarkStore


@pytest.mark.asyncio
async def test_first_join_invariant(tmp_path: Path):
    store = BenchmarkStore(tmp_path / "bench.db")
    await store.init()
    try:
        # Nothing recorded → first-join has not happened
        assert await store.has_first_join_run("worker-1") is False

        # Record a first_join=True row
        now = time.time()
        await store.record(
            worker_id="worker-1",
            worker_name="pi4",
            platform="linux-aarch64",
            capability="embedding",
            model="bge-small-en-v1.5",
            metric="docs_per_sec",
            value=42.5,
            unit="docs/s",
            status="ok",
            elapsed_seconds=1.2,
            error=None,
            details={"num_docs": 50},
            suite_name="default",
            first_join=True,
            measured_at=now,
        )

        # Now the invariant says a first-join has happened
        assert await store.has_first_join_run("worker-1") is True

        # A second worker is independent
        assert await store.has_first_join_run("worker-2") is False

        # A manual rerun can still record (with first_join=False)
        await store.record(
            worker_id="worker-1",
            worker_name="pi4",
            platform="linux-aarch64",
            capability="embedding",
            model="bge-small-en-v1.5",
            metric="docs_per_sec",
            value=48.2,
            unit="docs/s",
            status="ok",
            elapsed_seconds=1.1,
            error=None,
            details=None,
            suite_name="default",
            first_join=False,
            measured_at=now + 60,
        )

        # Invariant still holds — exactly one first_join record
        history = await store.history_by_worker("worker-1")
        first_joins = [h for h in history if h["first_join"]]
        assert len(first_joins) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_latest_by_worker_dedupes(tmp_path: Path):
    store = BenchmarkStore(tmp_path / "bench.db")
    await store.init()
    try:
        now = time.time()
        # Two runs of the same task
        await store.record(
            worker_id="w", worker_name="w", platform="p",
            capability="embedding", model="m", metric="docs_per_sec",
            value=10.0, unit="docs/s", status="ok", elapsed_seconds=1.0,
            error=None, details=None, suite_name="s",
            first_join=True, measured_at=now,
        )
        await store.record(
            worker_id="w", worker_name="w", platform="p",
            capability="embedding", model="m", metric="docs_per_sec",
            value=12.0, unit="docs/s", status="ok", elapsed_seconds=1.0,
            error=None, details=None, suite_name="s",
            first_join=False, measured_at=now + 10,
        )
        # Different task on the same worker
        await store.record(
            worker_id="w", worker_name="w", platform="p",
            capability="llm-chat", model="tinyllama-1.1b", metric="tokens_per_sec",
            value=33.0, unit="tok/s", status="ok", elapsed_seconds=5.0,
            error=None, details=None, suite_name="s",
            first_join=False, measured_at=now + 20,
        )

        latest = await store.latest_by_worker("w")
        # One row per (capability, model) — 2 rows total, with the newest
        # embedding value (12.0)
        assert len(latest) == 2
        embed_row = next(r for r in latest if r["capability"] == "embedding")
        assert embed_row["value"] == 12.0
        llm_row = next(r for r in latest if r["capability"] == "llm-chat")
        assert llm_row["value"] == 33.0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_leaderboard_ranks_workers(tmp_path: Path):
    store = BenchmarkStore(tmp_path / "bench.db")
    await store.init()
    try:
        now = time.time()
        for wid, val in [("pi", 15.0), ("mac", 90.0), ("fedora-gpu", 200.0)]:
            await store.record(
                worker_id=wid, worker_name=wid, platform="p",
                capability="embedding", model="bge-small-en-v1.5",
                metric="docs_per_sec", value=val, unit="docs/s",
                status="ok", elapsed_seconds=1.0, error=None,
                details=None, suite_name="default",
                first_join=True, measured_at=now,
            )

        leaderboard = await store.leaderboard("embedding", metric="docs_per_sec")
        assert [r["worker_id"] for r in leaderboard] == ["fedora-gpu", "mac", "pi"]
        assert leaderboard[0]["value"] == 200.0
    finally:
        await store.close()
