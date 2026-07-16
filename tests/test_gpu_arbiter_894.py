"""Tests for GPU arbiter eviction preemption (taOS #894 fix).

Verifies that _evict_task actually stops running GPU work by cancelling
the asyncio Task, not just the _arbiter_future.  Covers both the direct-
admission path and the queued-then-admitted path.
"""

import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter, _QueuedGpuTask
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


# ── Non-blocking drain + eviction-to-make-room ─────────────────────────

class TestDrainQueueNonBlocking:
    """_drain_queue spawns background tasks; doesn't block on _run_gpu_task."""

    @pytest.mark.asyncio
    async def test_drain_returns_immediately_after_spawning_task(self):
        """_drain_queue should return without waiting for the GPU task."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        payload_started = asyncio.Event()
        payload_done = asyncio.Event()

        async def slow_payload(_resource):
            payload_started.set()
            await payload_done.wait()  # Block until released
            return {"slow": "done"}

        task = _make_task("t-drain-fast", vram_mb=0)
        task.payload = slow_payload

        # Queue the task
        loop = asyncio.get_running_loop()
        arb_future: asyncio.Future = loop.create_future()
        task._arbiter_future = arb_future  # type: ignore[attr-defined]
        entry = _QueuedGpuTask(
            priority=int(task.priority), seq=1, task=task,
            required_vram_mb=0, evictable=False,
        )
        await arbiter._queue.put(entry)

        # Drain — should return immediately after spawning
        await arbiter._drain_queue()

        # Payload was started (spawned as background task)
        await asyncio.wait_for(payload_started.wait(), timeout=5)

        # _drain_queue returned — queue should be empty
        assert arbiter._queue.empty()

        # Task should be in _running (registered by _run_gpu_task)
        assert "t-drain-fast" in arbiter._running

        # Release the payload
        payload_done.set()

        # arbiter_future should be resolved
        result = await asyncio.wait_for(arb_future, timeout=5)
        assert result is not None

        # Cleanup — task should remove itself from _running on completion
        # (the _run_gpu_task finally block handles this)
        await asyncio.sleep(0.1)
        # After completion, _running should be empty (or at least not contain t-drain-fast)
        # Actually, with the new async model, _run_gpu_task removes itself from _running
        # on completion, so let's check:
        if "t-drain-fast" in arbiter._running:
            await arbiter._evict_task("t-drain-fast")

    @pytest.mark.asyncio
    async def test_done_callback_sets_arbiter_future_result(self):
        """When the background task completes, _arbiter_future gets the result."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        async def quick_payload(_resource):
            return {"answer": 42}

        task = _make_task("t-done-cb", vram_mb=0)
        task.payload = quick_payload

        loop = asyncio.get_running_loop()
        arb_future: asyncio.Future = loop.create_future()
        task._arbiter_future = arb_future  # type: ignore[attr-defined]

        entry = _QueuedGpuTask(
            priority=int(task.priority), seq=1, task=task,
            required_vram_mb=0, evictable=False,
        )
        await arbiter._queue.put(entry)

        await arbiter._drain_queue()

        result = await asyncio.wait_for(arb_future, timeout=5)
        assert result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_done_callback_propagates_exception(self):
        """When the background task raises, the exception propagates to the future."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        async def failing_payload(_resource):
            raise ValueError("boom")

        task = _make_task("t-fail-cb", vram_mb=0)
        task.payload = failing_payload

        loop = asyncio.get_running_loop()
        arb_future: asyncio.Future = loop.create_future()
        task._arbiter_future = arb_future  # type: ignore[attr-defined]

        entry = _QueuedGpuTask(
            priority=int(task.priority), seq=1, task=task,
            required_vram_mb=0, evictable=False,
        )
        await arbiter._queue.put(entry)

        await arbiter._drain_queue()

        with pytest.raises(ValueError, match="boom"):
            await asyncio.wait_for(arb_future, timeout=5)


class TestEvictionToMakeRoom:
    """When admission fails, _drain_queue tries eviction before re-queuing."""

    @pytest.mark.asyncio
    async def test_eviction_triggered_when_vram_full(self):
        """If VRAM is full, drain should evict a lower-priority task to make room."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (
                (1024, 8192) if len(arbiter_ctx["ref"]._running) > 0 else (8192, 8192)
            ),
            max_queue_size=10,
        )
        arbiter_ctx: dict[str, object] = {"ref": arbiter}

        # Put a low-priority task in _running (simulating an already-running task)
        low_task = _make_task("low-running", vram_mb=4096, priority=Priority.BATCH)
        low_task.payload = lambda r: asyncio.sleep(60)
        arbiter._running["low-running"] = (low_task, None, int(Priority.BATCH), 4096)

        # Now queue a higher-priority task that needs VRAM
        hi_task = _make_task("hi-queued", vram_mb=4096, priority=Priority.INTERACTIVE_USER)
        hi_started = asyncio.Event()
        hi_release = asyncio.Event()

        async def hi_payload(_resource):
            hi_started.set()
            await hi_release.wait()
            return {"ok": True}

        hi_task.payload = hi_payload

        loop = asyncio.get_running_loop()
        arb_future: asyncio.Future = loop.create_future()
        hi_task._arbiter_future = arb_future  # type: ignore[attr-defined]

        entry = _QueuedGpuTask(
            priority=int(hi_task.priority), seq=1, task=hi_task,
            required_vram_mb=4096, evictable=False,
        )
        await arbiter._queue.put(entry)

        # Drain — should evict "low-running" and admit "hi-queued"
        await arbiter._drain_queue()

        # Yield so the background task can register in _running
        await asyncio.sleep(0)

        # Low-priority task should be evicted
        assert "low-running" not in arbiter._running, (
            f"Expected low-priority task to be evicted, but _running={list(arbiter._running)}"
        )

        # High-priority task should be running
        assert "hi-queued" in arbiter._running

        # Release the payload and wait for result
        hi_release.set()
        result = await asyncio.wait_for(arb_future, timeout=5)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_no_eviction_when_all_running_higher_priority(self):
        """Don't evict tasks that are higher priority than the queued task."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (1024, 8192),  # Some free VRAM but not enough
            max_queue_size=10,
        )

        # Put a high-priority task in _running
        hi_task = _make_task("hi-running", vram_mb=4096, priority=Priority.INTERACTIVE_USER)
        hi_task.payload = lambda r: asyncio.sleep(60)
        arbiter._running["hi-running"] = (hi_task, None, int(Priority.INTERACTIVE_USER), 4096)

        # Queue a lower-priority task
        lo_task = _make_task("lo-queued", vram_mb=4096, priority=Priority.BATCH)

        loop = asyncio.get_running_loop()
        arb_future: asyncio.Future = loop.create_future()
        lo_task._arbiter_future = arb_future  # type: ignore[attr-defined]
        lo_task.payload = lambda r: asyncio.sleep(0)

        entry = _QueuedGpuTask(
            priority=int(lo_task.priority), seq=1, task=lo_task,
            required_vram_mb=4096, evictable=False,
        )
        await arbiter._queue.put(entry)

        # Drain — should NOT evict the higher-priority running task
        await arbiter._drain_queue()

        # High-priority task should still be running
        assert "hi-running" in arbiter._running

        # Low-priority task should be back in the queue (couldn't be admitted)
        assert not arbiter._queue.empty()
        re_queued = arbiter._queue.get_nowait()
        assert re_queued.task.id == "lo-queued"

        # Cleanup
        await arbiter._evict_task("hi-running")

    @pytest.mark.asyncio
    async def test_eviction_disabled_no_eviction(self):
        """When eviction is disabled, don't evict even if VRAM is full."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (1024, 8192),  # Some free VRAM but not enough
            max_queue_size=10,
            eviction_enabled=False,
        )

        # Put a task in _running
        run_task = _make_task("running", vram_mb=4096, priority=Priority.BATCH)
        run_task.payload = lambda r: asyncio.sleep(60)
        arbiter._running["running"] = (run_task, None, int(Priority.BATCH), 4096)

        # Queue a higher-priority task
        hi_task = _make_task("hi-queued", vram_mb=4096, priority=Priority.INTERACTIVE_USER)
        hi_task.payload = lambda r: asyncio.sleep(0)

        loop = asyncio.get_running_loop()
        arb_future: asyncio.Future = loop.create_future()
        hi_task._arbiter_future = arb_future  # type: ignore[attr-defined]

        entry = _QueuedGpuTask(
            priority=int(hi_task.priority), seq=1, task=hi_task,
            required_vram_mb=4096, evictable=False,
        )
        await arbiter._queue.put(entry)

        await arbiter._drain_queue()

        # Running task should NOT be evicted
        assert "running" in arbiter._running

        # Queued task should be back in the queue
        assert not arbiter._queue.empty()

        # Cleanup
        await arbiter._evict_task("running")


class TestEvictionOrdering:
    """Xid-62 fix: _evict_task awaits the cancelled task so its VRAM is
    physically reclaimed BEFORE the reservation is freed and re-admission is
    allowed (taOS #894)."""

    @pytest.mark.asyncio
    async def test_evict_awaits_task_before_releasing_reservation(self):
        arbiter = GpuArbiter(vram_probe=lambda: (8192, 8192), max_queue_size=10)

        started = asyncio.Event()
        unloaded = asyncio.Event()

        async def payload(_resource):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                # Stands in for the model's physical unload during teardown.
                unloaded.set()

        task = _make_task("t-order", vram_mb=0)
        task.payload = payload

        # Reserve against the shared ledger exactly as _reserve_and_check would.
        reservation = await arbiter._vram.reserve(4096, caller="gpu-task:t-order")
        arbiter._pending_reservations["t-order"] = reservation.reservation_id
        assert arbiter._vram.reserved_vram_mb == 4096

        bg = asyncio.create_task(arbiter._run_gpu_task(task, 4096, True, None))
        await asyncio.wait_for(started.wait(), timeout=5)

        await arbiter._evict_task("t-order")

        # The fix: the task coroutine has fully unwound (unload ran) and the
        # reservation is released by the time _evict_task returns — so a
        # re-admission cannot pass against not-yet-reclaimed VRAM.
        assert unloaded.is_set()
        assert bg.done()
        assert arbiter._vram.reserved_vram_mb == 0
        assert "t-order" not in arbiter._pending_reservations
