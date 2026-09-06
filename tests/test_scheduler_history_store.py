"""Unit tests for the scheduler HistoryStore.

Covers:
- Terminal transitions are persisted; non-terminal (queued/running) are not
- Round-trip through init → record_terminal → since
- by_resource and by_capability narrow views
- stats() aggregation
- Round-trip through close + reinit preserves data
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from tinyagentos.scheduler.history_store import HistoryStore
from tinyagentos.scheduler.types import TaskRecord, TaskStatus


def _record(
    *,
    task_id: str = "tid",
    capability: str = "image-generation",
    submitter: str = "test",
    priority: int = 10,
    resource: str | None = "npu-rk3588",
    status: TaskStatus = TaskStatus.COMPLETE,
    submitted_at: float | None = None,
    elapsed_seconds: float | None = 1.5,
    error: str | None = None,
) -> TaskRecord:
    now = time.time() if submitted_at is None else submitted_at
    return TaskRecord(
        task_id=task_id,
        capability=capability,
        submitter=submitter,
        priority=priority,
        resource=resource,
        status=status,
        submitted_at=now,
        started_at=now + 0.1,
        completed_at=now + elapsed_seconds if elapsed_seconds else None,
        elapsed_seconds=elapsed_seconds,
        error=error,
    )


@pytest.mark.asyncio
async def test_terminal_states_are_persisted(tmp_path: Path):
    store = HistoryStore(tmp_path / "h.db")
    await store.init()
    try:
        now = time.time()
        await store.record_terminal(_record(task_id="a", status=TaskStatus.COMPLETE, submitted_at=now))
        await store.record_terminal(_record(task_id="b", status=TaskStatus.ERROR, submitted_at=now + 1, error="boom"))
        await store.record_terminal(_record(task_id="c", status=TaskStatus.REJECTED, submitted_at=now + 2))

        rows = await store.since(now - 1)
        statuses = {r["task_id"]: r["status"] for r in rows}
        assert statuses == {"a": "complete", "b": "error", "c": "rejected"}
        err_row = next(r for r in rows if r["task_id"] == "b")
        assert err_row["error"] == "boom"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_non_terminal_states_are_skipped(tmp_path: Path):
    """Queued and running states must NOT be persisted — they're ephemeral."""
    store = HistoryStore(tmp_path / "h.db")
    await store.init()
    try:
        now = time.time()
        await store.record_terminal(_record(task_id="q", status=TaskStatus.QUEUED, submitted_at=now))
        await store.record_terminal(_record(task_id="r", status=TaskStatus.RUNNING, submitted_at=now))

        rows = await store.since(now - 1)
        assert rows == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_since_returns_newest_first(tmp_path: Path):
    store = HistoryStore(tmp_path / "h.db")
    await store.init()
    try:
        now = time.time()
        for i, tid in enumerate(("old", "mid", "new")):
            await store.record_terminal(_record(task_id=tid, submitted_at=now + i))
        rows = await store.since(now - 1)
        assert [r["task_id"] for r in rows] == ["new", "mid", "old"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_by_resource_narrows(tmp_path: Path):
    store = HistoryStore(tmp_path / "h.db")
    await store.init()
    try:
        now = time.time()
        await store.record_terminal(_record(task_id="n1", resource="npu-rk3588", submitted_at=now))
        await store.record_terminal(_record(task_id="c1", resource="cpu-inference", submitted_at=now + 1))
        await store.record_terminal(_record(task_id="n2", resource="npu-rk3588", submitted_at=now + 2))

        npu_rows = await store.by_resource("npu-rk3588")
        assert {r["task_id"] for r in npu_rows} == {"n1", "n2"}
        cpu_rows = await store.by_resource("cpu-inference")
        assert {r["task_id"] for r in cpu_rows} == {"c1"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_by_capability_narrows(tmp_path: Path):
    store = HistoryStore(tmp_path / "h.db")
    await store.init()
    try:
        now = time.time()
        await store.record_terminal(_record(task_id="i1", capability="image-generation", submitted_at=now))
        await store.record_terminal(_record(task_id="e1", capability="embedding", submitted_at=now + 1))

        img_rows = await store.by_capability("image-generation")
        assert [r["task_id"] for r in img_rows] == ["i1"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stats_aggregates(tmp_path: Path):
    store = HistoryStore(tmp_path / "h.db")
    await store.init()
    try:
        now = time.time()
        await store.record_terminal(_record(task_id="a", status=TaskStatus.COMPLETE, submitted_at=now))
        await store.record_terminal(_record(task_id="b", status=TaskStatus.COMPLETE, submitted_at=now))
        await store.record_terminal(_record(task_id="c", status=TaskStatus.ERROR, submitted_at=now))

        stats = await store.stats()
        assert stats["total"] == 3
        assert stats["by_status"] == {"complete": 2, "error": 1}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_round_trip_close_reinit(tmp_path: Path):
    """Data survives close + reopen — this is the whole point of persistence."""
    path = tmp_path / "h.db"
    store = HistoryStore(path)
    await store.init()
    now = time.time()
    await store.record_terminal(_record(task_id="survives", submitted_at=now))
    await store.close()

    # New store on the same path
    store2 = HistoryStore(path)
    await store2.init()
    try:
        rows = await store2.since(now - 1)
        assert [r["task_id"] for r in rows] == ["survives"]
    finally:
        await store2.close()
