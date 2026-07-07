"""Tests for the GPU arbiter — admission, queuing, eviction, and
drain→arbiter wiring (taOS #1707)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tinyagentos.scheduler.gpu_arbiter import (
    GpuAdmission,
    GpuArbiter,
    _DrainState,
)
from tinyagentos.scheduler.types import (
    Capability,
    NoResourceAvailableError,
    Priority,
    ResourceRef,
    Task,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str = "t1",
    priority: Priority = Priority.INTERACTIVE_AGENT,
) -> Task:
    return Task(
        id=task_id,
        capability=Capability.LLM_CHAT,
        priority=priority,
        submitter="test",
        payload=AsyncMock(return_value="ok"),
        preferred_resources=[],
    )


def _vram_probe(free_mb: int, total_mb: int = 8192):
    """Return a probe that always returns (free_mb, total_mb)."""
    def _probe() -> tuple[int, int]:
        return free_mb, total_mb
    return _probe


# ---------------------------------------------------------------------------
# GpuAdmission
# ---------------------------------------------------------------------------

class TestGpuAdmission:
    def test_admitted_defaults(self):
        a = GpuAdmission(admitted=True)
        assert a.admitted is True
        assert a.reason is None
        assert a.free_vram_mb == 0

    def test_rejected_with_reason(self):
        a = GpuAdmission(
            admitted=False, free_vram_mb=1024, required_vram_mb=4096,
            reason="insufficient VRAM",
        )
        assert a.admitted is False
        assert a.reason == "insufficient VRAM"


# ---------------------------------------------------------------------------
# GpuArbiter — admission
# ---------------------------------------------------------------------------

class TestGpuArbiterAdmission:
    @pytest.mark.asyncio
    async def test_zero_vram_always_admitted(self):
        arbiter = GpuArbiter(vram_probe=_vram_probe(0))
        task = _make_task()
        result = await arbiter.submit_gpu(task, required_vram_mb=0)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_sufficient_vram_admitted(self):
        arbiter = GpuArbiter(vram_probe=_vram_probe(4096))
        task = _make_task()
        result = await arbiter.submit_gpu(task, required_vram_mb=2048)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_insufficient_vram_queued(self):
        arbiter = GpuArbiter(vram_probe=_vram_probe(1024), max_queue_size=10)
        task = _make_task()
        # VRAM is insufficient — task will queue. The _arbiter_future
        # won't resolve because no queue processor is running.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                arbiter.submit_gpu(task, required_vram_mb=4096),
                timeout=0.5,
            )
        stats = await arbiter.stats()
        assert stats["submitted"] == 1
        assert stats["queued"] == 1

    @pytest.mark.asyncio
    async def test_queue_full_raises(self):
        arbiter = GpuArbiter(vram_probe=_vram_probe(1024), max_queue_size=1)
        t1 = _make_task("t1")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                arbiter.submit_gpu(t1, required_vram_mb=4096),
                timeout=0.3,
            )
        t2 = _make_task("t2")
        with pytest.raises(NoResourceAvailableError, match="queue full"):
            await arbiter.submit_gpu(t2, required_vram_mb=4096)


# ---------------------------------------------------------------------------
# GpuArbiter — eviction
# ---------------------------------------------------------------------------

class TestGpuArbiterEviction:
    @pytest.mark.asyncio
    async def test_evict_lowest_priority(self):
        arbiter = GpuArbiter(vram_probe=_vram_probe(8192))

        # Manually seed a running task (bypass admission).
        # Priority.BACKGROUND = 30 (higher value = lower priority = evicted first)
        task = _make_task("t-victim", priority=Priority.BACKGROUND)
        async with arbiter._running_lock:
            arbiter._running[task.id] = (task, None, int(task.priority), 2048)

        evicted = await arbiter.evict_lowest_priority()
        assert evicted == 1
        stats = await arbiter.stats()
        assert stats["evicted"] == 1
        assert stats["running"] == 0

    @pytest.mark.asyncio
    async def test_evict_respects_min_priority(self):
        arbiter = GpuArbiter(vram_probe=_vram_probe(8192))

        # Priority.INTERACTIVE_USER = 10 (low number = high priority)
        task = _make_task("t-important", priority=Priority.INTERACTIVE_USER)
        async with arbiter._running_lock:
            arbiter._running[task.id] = (task, None, int(task.priority), 2048)

        # min_priority=20 means only evict tasks with pri >= 20.
        # INTERACTIVE_USER is 10, so it should be skipped.
        evicted = await arbiter.evict_lowest_priority(min_priority=20)
        assert evicted == 0
        stats = await arbiter.stats()
        assert stats["running"] == 1

    @pytest.mark.asyncio
    async def test_evict_highest_priority_value_wins(self):
        """When multiple tasks run, the one with highest numeric priority
        (lowest actual priority) gets evicted."""
        arbiter = GpuArbiter(vram_probe=_vram_probe(8192))

        t_low = _make_task("t-low", priority=Priority.BATCH)       # pri=40
        t_med = _make_task("t-med", priority=Priority.BACKGROUND)   # pri=30
        t_high = _make_task("t-high", priority=Priority.INTERACTIVE_USER)  # pri=10

        async with arbiter._running_lock:
            arbiter._running["t-low"] = (t_low, None, 40, 1024)
            arbiter._running["t-med"] = (t_med, None, 30, 2048)
            arbiter._running["t-high"] = (t_high, None, 10, 512)

        evicted = await arbiter.evict_lowest_priority()
        assert evicted == 1
        # t-low (pri=40) should be evicted
        stats = await arbiter.stats()
        assert stats["evicted"] == 1
        assert stats["running"] == 2

    @pytest.mark.asyncio
    async def test_evict_not_found(self):
        arbiter = GpuArbiter()
        evicted = await arbiter.evict_lowest_priority()
        assert evicted == 0

    @pytest.mark.asyncio
    async def test_eviction_disabled(self):
        arbiter = GpuArbiter(eviction_enabled=False)
        task = _make_task()
        async with arbiter._running_lock:
            arbiter._running[task.id] = (task, None, int(task.priority), 2048)
        evicted = await arbiter.evict_lowest_priority()
        assert evicted == 0


# ---------------------------------------------------------------------------
# GpuArbiter — drain→arbiter wiring (taOS #1707)
# ---------------------------------------------------------------------------

class TestGpuArbiterDrainWiring:
    @pytest.mark.asyncio
    async def test_on_drain_complete_returns_true(self):
        arbiter = GpuArbiter()
        state = _DrainState(model_id="m1")
        arbiter._draining["m1"] = state
        result = await arbiter.on_drain_complete("m1")
        assert result is True
        assert state.event.is_set()
        assert "m1" not in arbiter._draining

    @pytest.mark.asyncio
    async def test_on_drain_complete_unknown_model(self):
        arbiter = GpuArbiter()
        result = await arbiter.on_drain_complete("unknown")
        assert result is False

    @pytest.mark.asyncio
    async def test_notify_drain_and_wait_no_fn_proceeds(self):
        """Without drain_notify_fn, _notify_drain_and_wait returns True."""
        arbiter = GpuArbiter(drain_notify_fn=None)
        result = await arbiter._notify_drain_and_wait("m1")
        assert result is True

    @pytest.mark.asyncio
    async def test_notify_drain_and_wait_with_fn(self):
        """With drain_notify_fn, waits for on_drain_complete."""
        drain_calls: list[str] = []

        async def drain_fn(model_id: str) -> None:
            drain_calls.append(model_id)

        arbiter = GpuArbiter(drain_notify_fn=drain_fn, drain_timeout=5.0)

        async def do_drain() -> bool:
            return await arbiter._notify_drain_and_wait("m1")

        drain_task = asyncio.create_task(do_drain())
        await asyncio.sleep(0.05)

        assert drain_calls == ["m1"]

        result = await arbiter.on_drain_complete("m1")
        assert result is True

        drain_result = await asyncio.wait_for(drain_task, timeout=1.0)
        assert drain_result is True

    @pytest.mark.asyncio
    async def test_notify_drain_timeout(self):
        """When drain doesn't complete within timeout, returns False."""
        async def slow_drain(model_id: str) -> None:
            pass  # never calls on_drain_complete

        arbiter = GpuArbiter(drain_notify_fn=slow_drain, drain_timeout=0.1)
        result = await arbiter._notify_drain_and_wait("m1")
        assert result is False

    @pytest.mark.asyncio
    async def test_evict_task_notifies_drain(self):
        """_evict_task calls _notify_drain_and_wait before cancelling."""
        drain_calls: list[str] = []

        async def drain_fn(model_id: str) -> None:
            drain_calls.append(model_id)
            # Complete drain asynchronously
            asyncio.get_running_loop().create_task(
                _complete_drain(arbiter, model_id)
            )

        arbiter = GpuArbiter(
            drain_notify_fn=drain_fn, drain_timeout=5.0,
            vram_probe=_vram_probe(8192),
        )

        task = _make_task("t-drain-victim")
        # Store model_id as an attribute (real tasks won't have this;
        # the arbiter uses getattr fallback)
        task.model_id = "my-model"  # type: ignore[attr-defined]
        async with arbiter._running_lock:
            arbiter._running[task.id] = (task, None, int(task.priority), 2048)

        evicted = await arbiter._evict_task(task.id)
        assert evicted == 1
        assert drain_calls == ["my-model"]
        stats = await arbiter.stats()
        assert stats["evicted"] == 1

    @pytest.mark.asyncio
    async def test_evict_task_falls_back_to_task_id(self):
        """When task has no model_id, uses task.id for drain notification."""
        drain_calls: list[str] = []

        async def drain_fn(model_id: str) -> None:
            drain_calls.append(model_id)
            asyncio.get_running_loop().create_task(
                _complete_drain(arbiter, model_id)
            )

        arbiter = GpuArbiter(drain_notify_fn=drain_fn, drain_timeout=5.0)

        task = _make_task("t-fallback-id")
        # No model_id attribute — falls back to task.id
        async with arbiter._running_lock:
            arbiter._running[task.id] = (task, None, int(task.priority), 2048)

        evicted = await arbiter._evict_task(task.id)
        assert evicted == 1
        assert drain_calls == ["t-fallback-id"]


async def _complete_drain(arbiter: GpuArbiter, model_id: str) -> None:
    """Helper to complete a drain after a short delay."""
    await asyncio.sleep(0.01)
    await arbiter.on_drain_complete(model_id)


# ---------------------------------------------------------------------------
# GpuArbiter — cancel_running_for_leases
# ---------------------------------------------------------------------------

class TestGpuArbiterLeaseCancel:
    @pytest.mark.asyncio
    async def test_cancel_by_lease_ids(self):
        arbiter = GpuArbiter()

        t1 = _make_task("t1")
        t2 = _make_task("t2")
        t3 = _make_task("t3")

        async with arbiter._running_lock:
            arbiter._running["t1"] = (t1, "lease-a", 0, 1024)
            arbiter._running["t2"] = (t2, "lease-b", 0, 2048)
            arbiter._running["t3"] = (t3, "lease-c", 0, 512)

        cancelled, done = await arbiter.cancel_running_for_leases(
            {"lease-a", "lease-c"}
        )
        assert cancelled == 2
        assert done == 0
        stats = await arbiter.stats()
        assert stats["running"] == 1

    @pytest.mark.asyncio
    async def test_cancel_empty_leases(self):
        arbiter = GpuArbiter()
        cancelled, done = await arbiter.cancel_running_for_leases(set())
        assert cancelled == 0
        assert done == 0

    @pytest.mark.asyncio
    async def test_cancel_no_match(self):
        arbiter = GpuArbiter()
        t1 = _make_task("t1")
        async with arbiter._running_lock:
            arbiter._running["t1"] = (t1, "lease-a", 0, 1024)
        cancelled, done = await arbiter.cancel_running_for_leases({"lease-x"})
        assert cancelled == 0
        assert done == 0


# ---------------------------------------------------------------------------
# GpuArbiter — pause / resume
# ---------------------------------------------------------------------------

class TestGpuArbiterPause:
    def test_pause_resume(self):
        arbiter = GpuArbiter()
        assert arbiter.paused is False

        result = arbiter.pause()
        assert result is True
        assert arbiter.paused is True

        result = arbiter.pause()
        assert result is False

        result = arbiter.resume()
        assert result is True
        assert arbiter.paused is False

        result = arbiter.resume()
        assert result is False


# ---------------------------------------------------------------------------
# GpuArbiter — stats and observability
# ---------------------------------------------------------------------------

class TestGpuArbiterStats:
    @pytest.mark.asyncio
    async def test_initial_stats(self):
        arbiter = GpuArbiter()
        stats = await arbiter.stats()
        assert stats["submitted"] == 0
        assert stats["admitted"] == 0
        assert stats["evicted"] == 0
        assert stats["running"] == 0
        assert stats["active_drains"] == 0

    @pytest.mark.asyncio
    async def test_running_tasks_empty(self):
        arbiter = GpuArbiter()
        tasks = await arbiter.running_tasks()
        assert tasks == []

    def test_queue_snapshot_empty(self):
        arbiter = GpuArbiter()
        snap = arbiter.queue_snapshot()
        assert snap == []
