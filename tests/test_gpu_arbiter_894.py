"""Tests for GPU arbiter eviction preemption (taOS #894 fix).

Verifies that _evict_task actually stops running GPU work by cancelling
the asyncio Task, not just the _arbiter_future.  Covers both the direct-
admission path and the queued-then-admitted path.
"""

import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_task(task_id="t1", vram_mb=0, priority=Priority.INTERACTIVE_AGENT):
    return Task(
        id=task_id, capability=Capability.LLM_CHAT,
        payload=lambda r: asyncio.sleep(0),
        preferred_resources=[], priority=priority,
        estimated_vram_mb=vram_mb,
    )


# ── Direct-admission eviction ──────────────────────────────────────────

class TestDirectAdmissionEviction:
    """Eviction of tasks admitted directly (no queue)."""

    @pytest.mark.asyncio
    async def test_evict_directly_admitted_task_cancels_submitter(self):
        """A directly-admitted task should be preempted by eviction."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),  # 8 GiB free
            max_queue_size=10,
        )

        # Long-running payload that never finishes
        payload_started = asyncio.Event()
        payload_cancelled = asyncio.Event()

        async def long_running(_resource):
            payload_started.set()
            try:
                await asyncio.sleep(60)  # won't actually finish
            except asyncio.CancelledError:
                payload_cancelled.set()
                raise

        task = _make_task("t-direct", vram_mb=0)
        task.payload = long_running

        # Submit in background so we can evict while it runs
        async def submitter():
            try:
                await arbiter.submit_gpu(task, required_vram_mb=0)
            except asyncio.CancelledError:
                return "cancelled"
            return "completed"

        submit_coro = asyncio.create_task(submitter())

        # Wait for payload to actually start
        await asyncio.wait_for(payload_started.wait(), timeout=5)

        # Evict the running task
        evicted = await arbiter._evict_task("t-direct")
        assert evicted == 1

        # Submitter should get CancelledError
        result = await submit_coro
        assert result == "cancelled"

        # Payload should have been cancelled
        assert payload_cancelled.is_set()

        # Task should not be in _running anymore
        assert "t-direct" not in arbiter._running
        assert "t-direct" not in arbiter._running_tasks

    @pytest.mark.asyncio
    async def test_evict_frees_running_slot(self):
        """After eviction, the task is removed from _running."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)

        task = _make_task("t-slot", vram_mb=0)
        task.payload = blocking

        async def submitter():
            try:
                await arbiter.submit_gpu(task, required_vram_mb=0)
            except asyncio.CancelledError:
                pass

        submit_coro = asyncio.create_task(submitter())
        await asyncio.wait_for(started.wait(), timeout=5)

        assert "t-slot" in arbiter._running
        assert len(arbiter._running) == 1

        await arbiter._evict_task("t-slot")

        await submit_coro
        assert "t-slot" not in arbiter._running

    @pytest.mark.asyncio
    async def test_evict_nonexistent_task_returns_zero(self):
        """Evicting a non-existent task returns 0."""
        arbiter = GpuArbiter()
        assert (await arbiter._evict_task("nonexistent")) == 0

    @pytest.mark.asyncio
    async def test_evict_eviction_disabled_returns_zero(self):
        """evict_lowest_priority returns 0 when eviction is disabled."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            eviction_enabled=False,
        )

        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)

        task = _make_task("t-noevict", vram_mb=0)
        task.payload = blocking

        async def submitter():
            try:
                await arbiter.submit_gpu(task, required_vram_mb=0)
            except asyncio.CancelledError:
                pass

        submit_coro = asyncio.create_task(submitter())
        await asyncio.wait_for(started.wait(), timeout=5)

        assert await arbiter.evict_lowest_priority() == 0
        assert "t-noevict" in arbiter._running

        # Clean up
        arbiter._running_tasks.get("t-noevict", None)
        submit_coro.cancel()
        try:
            await submit_coro
        except asyncio.CancelledError:
            pass


# ── Queued-task eviction ────────────────────────────────────────────────

class TestQueuedTaskEviction:
    """Eviction of tasks that were queued, then admitted via _drain_queue."""

    @pytest.mark.asyncio
    async def test_evict_queued_admitted_task_cancels_submitter(self):
        """A queued-then-admitted task should be preempted by eviction.

        Simulated: we manually call _run_gpu_task via _drain_queue path
        and evict while it's running.
        """
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        payload_cancelled = asyncio.Event()

        async def long_running(_resource):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                payload_cancelled.set()
                raise

        task = _make_task("t-queued", vram_mb=0)
        task.payload = long_running

        # Manually queue the task and run it like _drain_queue would
        async def runner():
            try:
                return await arbiter._run_gpu_task(task, 0, False, None)
            except asyncio.CancelledError:
                return "cancelled"

        runner_task = asyncio.create_task(runner())

        # Give it a moment to register in _running
        await asyncio.sleep(0.1)

        assert "t-queued" in arbiter._running

        # Evict
        evicted = await arbiter._evict_task("t-queued")
        assert evicted == 1

        result = await runner_task
        assert result == "cancelled"
        assert payload_cancelled.is_set()

    @pytest.mark.asyncio
    async def test_evict_with_arbiter_future_cancels_future(self):
        """_evict_task cancels _arbiter_future if present on the task."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)

        task = _make_task("t-future", vram_mb=0)
        task.payload = blocking

        # Attach an _arbiter_future (as submit_gpu does for queued tasks)
        loop = asyncio.get_running_loop()
        arb_future: asyncio.Future = loop.create_future()
        task._arbiter_future = arb_future  # type: ignore[attr-defined]

        async def submitter():
            try:
                await arbiter._run_gpu_task(task, 0, False, None)
            except asyncio.CancelledError:
                pass

        runner_task = asyncio.create_task(submitter())
        await asyncio.wait_for(started.wait(), timeout=5)

        # Evict
        await arbiter._evict_task("t-future")

        # The arbiter future should be cancelled
        assert arb_future.cancelled() is True
        assert arb_future.done() is True

        await runner_task


# ── Lease handling during eviction ──────────────────────────────────────

class FakeClusterManager:
    """Minimal fake for testing lease claim/release during eviction."""

    def __init__(self):
        self._leases: dict[str, object] = {}
        self.release_calls: list[str] = []
        self.claim_calls: list[str] = []

    class FakeLease:
        def __init__(self, lease_id: str, resource_id: str):
            self.lease_id = lease_id
            self.resource_id = resource_id

    async def claim_lease(self, resource_id, caller, ttl_seconds, required_vram_mb):
        lease_id = f"lease-{resource_id}"
        lease = self.FakeLease(lease_id, resource_id)
        self._leases[lease_id] = lease
        self.claim_calls.append(resource_id)
        return lease

    async def release_lease(self, lease_id):
        self._leases.pop(lease_id, None)
        self.release_calls.append(lease_id)

    def get_leases(self):
        return list(self._leases.values())

    def get_workers(self):
        return []


class TestLeaseHandling:
    """Lease claim/release behaviour during eviction."""

    @pytest.mark.asyncio
    async def test_evict_releases_lease_once(self):
        """Eviction releases the lease exactly once, not double-released."""
        cm = FakeClusterManager()
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            cluster_manager=cm,
            max_queue_size=10,
        )

        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)

        task = _make_task("t-lease", vram_mb=0)
        task.payload = blocking

        async def runner():
            try:
                await arbiter._run_gpu_task(task, 0, False, "gpu-cuda-0:t-lease")
            except asyncio.CancelledError:
                pass

        runner_task = asyncio.create_task(runner())
        await asyncio.wait_for(started.wait(), timeout=5)

        assert len(cm.claim_calls) == 1

        await arbiter._evict_task("t-lease")

        # Lease should be released exactly once by eviction
        # (the finally block in _run_gpu_task should NOT double-release
        # because the entry was already popped from _running)
        assert len(cm.release_calls) == 1, f"Expected 1 release, got {len(cm.release_calls)}"

        await runner_task

    @pytest.mark.asyncio
    async def test_evict_without_lease_no_double_free(self):
        """Eviction without a lease shouldn't cause errors."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            cluster_manager=None,  # No cluster manager
            max_queue_size=10,
        )

        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)

        task = _make_task("t-nolease", vram_mb=0)
        task.payload = blocking

        async def runner():
            try:
                await arbiter._run_gpu_task(task, 0, False, None)
            except asyncio.CancelledError:
                pass

        runner_task = asyncio.create_task(runner())
        await asyncio.wait_for(started.wait(), timeout=5)

        # Should not raise
        await arbiter._evict_task("t-nolease")
        await runner_task


# ── Low-priority eviction ──────────────────────────────────────────────

class TestEvictLowestPriority:
    """evict_lowest_priority selection logic."""

    @pytest.mark.asyncio
    async def test_evicts_lowest_priority_task(self):
        """evict_lowest_priority picks the highest priority value (lowest pri)."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        started_high = asyncio.Event()
        started_low = asyncio.Event()

        async def blocking_high(_resource):
            started_high.set()
            await asyncio.sleep(60)

        async def blocking_low(_resource):
            started_low.set()
            await asyncio.sleep(60)

        task_high = _make_task("high", vram_mb=0, priority=Priority.INTERACTIVE_USER)
        task_high.payload = blocking_high
        task_low = _make_task("low", vram_mb=0, priority=Priority.BATCH)
        task_low.payload = blocking_low

        async def runner(task):
            try:
                await arbiter._run_gpu_task(task, 0, False, None)
            except asyncio.CancelledError:
                pass

        runner_high = asyncio.create_task(runner(task_high))
        runner_low = asyncio.create_task(runner(task_low))

        await asyncio.wait_for(started_high.wait(), timeout=5)
        await asyncio.wait_for(started_low.wait(), timeout=5)

        # Both running
        assert len(arbiter._running) == 2

        # Evict lowest priority (BATCH > INTERACTIVE_USER in numeric value)
        evicted = await arbiter.evict_lowest_priority()
        assert evicted == 1

        # Low-priority (BATCH=50) should be evicted, high (INTERACTIVE_USER=10) stays
        assert "low" not in arbiter._running
        assert "high" in arbiter._running

        # Cleanup
        await arbiter._evict_task("high")
        await runner_high
        await runner_low
