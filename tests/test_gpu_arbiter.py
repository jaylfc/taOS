"""Tests for the GPU VRAM arbiter (taOS #894 Slice 2)."""
import asyncio

import pytest

from tinyagentos.scheduler.gpu_arbiter import GpuArbiter, VramAllocation
from tinyagentos.scheduler.resource import Resource, Tier
from tinyagentos.scheduler.types import (
    Capability, Priority, ResourceSignature, Task,
)


@pytest.fixture
def arbiter():
    return GpuArbiter(total_vram_mb=8192, headroom_mb=1024)


@pytest.fixture
def evict_log():
    return []


@pytest.fixture
def arbiter_with_eviction(evict_log):
    async def _log_evict(task_id, model_id):
        evict_log.append((task_id, model_id))
    return GpuArbiter(total_vram_mb=8192, headroom_mb=1024, evict_callback=_log_evict)


def _make_task(task_id="t1", vram_mb=0, priority=Priority.INTERACTIVE_AGENT):
    return Task(
        id=task_id, capability=Capability.LLM_CHAT,
        payload=lambda r: asyncio.sleep(0), preferred_resources=[],
        priority=priority, estimated_vram_mb=vram_mb,
    )


def _make_resource(name="gpu-cuda-0", arbiter=None):
    return Resource(
        name=name,
        signature=ResourceSignature(platform="cuda-sm_86", runtime="cuda"),
        concurrency=2,
        get_capabilities=lambda: {"llm-chat", "embedding", "image-generation"},
        backend_lookup=lambda c: "http://localhost:11434",
        tier=Tier.GPU, gpu_arbiter=arbiter,
    )


class TestVramAccounting:
    def test_initial_free(self, arbiter):
        assert arbiter.total_vram_mb == 8192
        assert arbiter.free_vram_mb == 7168
        assert arbiter.used_vram_mb == 0

    def test_reserve_updates_used(self, arbiter):
        assert arbiter.reserve("a", 2048)
        assert arbiter.used_vram_mb == 2048

    def test_release_frees(self, arbiter):
        arbiter.reserve("a", 2048)
        arbiter.release("a")
        assert arbiter.used_vram_mb == 0

    def test_release_idempotent(self, arbiter):
        arbiter.reserve("a", 2048)
        arbiter.release("a")
        assert arbiter.release("a") is None

    def test_insufficient_fails(self, arbiter):
        assert not arbiter.reserve("big", 8000)

    def test_zero_vram_always_ok(self, arbiter):
        assert arbiter.reserve("z", 0)
        assert arbiter.used_vram_mb == 0


class TestAdmission:
    def test_can_admit_ok(self, arbiter):
        ok, reason = arbiter.can_admit("t", 4096)
        assert ok and reason is None

    def test_can_admit_insufficient(self, arbiter):
        ok, reason = arbiter.can_admit("t", 8000)
        assert not ok and "insufficient VRAM" in reason

    def test_can_admit_after_allocation(self, arbiter):
        arbiter.reserve("a", 6000)
        ok, _ = arbiter.can_admit("b", 2000)
        assert not ok

    def test_own_allocation_not_double_counted(self, arbiter):
        arbiter.reserve("a", 4096)
        ok, _ = arbiter.can_admit("a", 4096)
        assert ok


class TestEviction:
    def test_no_candidates_empty(self, arbiter):
        assert arbiter.find_eviction_candidates(10, 4096) == []

    def test_cant_evict_higher_priority(self, arbiter):
        arbiter.reserve("a", 4096, priority=Priority.INTERACTIVE_USER)
        assert arbiter.find_eviction_candidates(20, 4096) == []

    def test_evicts_lowest_priority(self, arbiter):
        arbiter.reserve("a", 2048, priority=Priority.BATCH)
        arbiter.reserve("b", 2048, priority=Priority.BACKGROUND)
        candidates = arbiter.find_eviction_candidates(10, 2048)
        assert len(candidates) == 1 and candidates[0].task_id == "a"

    def test_non_evictable_ignored(self, arbiter):
        arbiter.reserve("a", 4096, priority=Priority.BATCH, evictable=False)
        assert arbiter.find_eviction_candidates(10, 4096) == []

    def test_multiple_for_large_need(self, arbiter):
        arbiter.reserve("a", 1024, priority=Priority.BATCH)
        arbiter.reserve("b", 1024, priority=Priority.BATCH)
        arbiter.reserve("c", 1024, priority=Priority.BATCH)
        candidates = arbiter.find_eviction_candidates(10, 2500)
        assert len(candidates) == 3

    @pytest.mark.asyncio
    async def test_evict_and_reserve_async(self, arbiter_with_eviction, evict_log):
        arbiter_with_eviction.reserve("low", 3000, priority=Priority.BATCH)
        arbiter_with_eviction.reserve("mid", 2000, priority=Priority.BACKGROUND)
        ok = await arbiter_with_eviction.evict_and_reserve(
            "high", 6000, "model-hi", Priority.INTERACTIVE_USER)
        assert ok
        assert "low" in [t for t, m in evict_log]
        allocs = arbiter_with_eviction.allocations
        assert any(a.task_id == "high" for a in allocs)
        assert not any(a.task_id == "low" for a in allocs)

    @pytest.mark.asyncio
    async def test_evict_and_reserve_no_candidates(self, arbiter):
        arbiter.reserve("a", 7000, priority=Priority.INTERACTIVE_USER, evictable=False)
        assert not await arbiter.evict_and_reserve("new", 2048, priority=20)


class TestWaitForVram:
    @pytest.mark.asyncio
    async def test_wakes_on_release(self, arbiter):
        arbiter.reserve("blocker", 7000)
        done = False
        async def waiter():
            nonlocal done
            await arbiter.wait_for_vram()
            done = True
        t = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)
        assert not done
        arbiter.release("blocker")
        await asyncio.sleep(0.02)
        assert done
        t.cancel()
        try: await t
        except asyncio.CancelledError: pass


class TestResourceIntegration:
    def test_can_admit_via_resource(self, arbiter):
        r = _make_resource(arbiter=arbiter)
        ok, _ = r.can_admit(_make_task(vram_mb=4096))
        assert ok

    def test_can_admit_rejects_vram(self, arbiter):
        r = _make_resource(arbiter=arbiter)
        ok, reason = r.can_admit(_make_task(vram_mb=8000))
        assert not ok and "insufficient VRAM" in reason

    def test_no_arbiter_no_vram_check(self):
        r = _make_resource(arbiter=None)
        ok, _ = r.can_admit(_make_task(vram_mb=999999))
        assert ok

    @pytest.mark.asyncio
    async def test_run_reserves_and_releases(self, arbiter):
        r = _make_resource(arbiter=arbiter)
        task = _make_task(vram_mb=4096)
        task.payload = lambda res: asyncio.sleep(0)
        await r.run(task)
        assert arbiter.used_vram_mb == 0

    @pytest.mark.asyncio
    async def test_run_releases_on_error(self, arbiter):
        r = _make_resource(arbiter=arbiter)
        task = _make_task(vram_mb=4096)
        async def fail(r): raise RuntimeError("boom")
        task.payload = fail
        with pytest.raises(RuntimeError, match="boom"):
            await r.run(task)
        assert arbiter.used_vram_mb == 0


class TestEdgeCases:
    def test_zero_total(self):
        a = GpuArbiter(total_vram_mb=0, headroom_mb=0)
        assert a.free_vram_mb == 0
        assert not a.can_admit("t", 1)[0]

    def test_large_vram(self):
        a = GpuArbiter(total_vram_mb=80 * 1024, headroom_mb=1024)
        assert a.can_admit("t", 40 * 1024)[0]

    def test_stats(self, arbiter):
        arbiter.reserve("a", 1024, "model-a")
        s = arbiter.stats()
        assert s["allocations"] == 1
        assert len(s["allocation_details"]) == 1

    def test_same_priority_fifo(self, arbiter):
        arbiter.reserve("a", 1024, priority=Priority.BATCH)
        import time; time.sleep(0.01)
        arbiter.reserve("b", 1024, priority=Priority.BATCH)
        candidates = arbiter.find_eviction_candidates(10, 1024)
        assert candidates[0].task_id == "a"
