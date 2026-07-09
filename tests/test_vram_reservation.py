"""Tests for VramReservationManager and its integration with GpuArbiter.

Covers:
- Atomic reserve/release semantics
- Concurrent reservation under contention
- TOCTOU closure: reserve fails when VRAM exhausted
- Integration: GpuArbiter reserves and releases VRAM through task lifecycle
- Eviction releases local VRAM reservation
- Stats reflect reservation state
"""
from __future__ import annotations

import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter, VramReservationManager
from tinyagentos.scheduler.types import Capability, NoResourceAvailableError, Priority, Task


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_task(task_id="t1", vram_mb=0, priority=Priority.INTERACTIVE_AGENT):
    return Task(
        id=task_id, capability=Capability.LLM_CHAT,
        payload=lambda r: asyncio.sleep(0),
        preferred_resources=[], priority=priority,
        estimated_vram_mb=vram_mb,
    )


# ── VramReservationManager unit tests ───────────────────────────────────

class TestVramReservationManager:
    """Unit tests for the reservation manager in isolation."""

    @pytest.mark.asyncio
    async def test_reserve_succeeds_when_vram_available(self):
        """Reserve returns True when sufficient VRAM is free."""
        mgr = VramReservationManager(vram_probe=lambda: (8192, 16384))
        assert await mgr.reserve("local", 4096) is True
        assert mgr.total_reserved("local") == 4096
        assert mgr.available("local") == 4096  # 8192 - 4096

    @pytest.mark.asyncio
    async def test_reserve_fails_when_vram_insufficient(self):
        """Reserve returns False when VRAM is insufficient."""
        mgr = VramReservationManager(vram_probe=lambda: (2048, 16384))
        assert await mgr.reserve("local", 4096) is False
        assert mgr.total_reserved("local") == 0
        assert mgr.available("local") == 2048

    @pytest.mark.asyncio
    async def test_reserve_zero_vram_always_succeeds(self):
        """Reserve with 0 VRAM always returns True."""
        mgr = VramReservationManager(vram_probe=lambda: (0, 0))
        assert await mgr.reserve("local", 0) is True
        assert mgr.total_reserved("local") == 0

    @pytest.mark.asyncio
    async def test_release_frees_reservation(self):
        """Release returns reserved VRAM to the pool."""
        mgr = VramReservationManager(vram_probe=lambda: (8192, 16384))
        await mgr.reserve("local", 4096)
        assert mgr.total_reserved("local") == 4096
        await mgr.release("local", 4096)
        assert mgr.total_reserved("local") == 0
        assert mgr.available("local") == 8192

    @pytest.mark.asyncio
    async def test_release_partial(self):
        """Release of partial reservation works correctly."""
        mgr = VramReservationManager(vram_probe=lambda: (8192, 16384))
        await mgr.reserve("local", 4096)
        await mgr.reserve("local", 2048)
        assert mgr.total_reserved("local") == 6144
        await mgr.release("local", 2048)
        assert mgr.total_reserved("local") == 4096

    @pytest.mark.asyncio
    async def test_release_idempotent_never_below_zero(self):
        """Release is idempotent — never goes negative."""
        mgr = VramReservationManager(vram_probe=lambda: (8192, 16384))
        await mgr.release("local", 4096)  # nothing reserved
        assert mgr.total_reserved("local") == 0
        await mgr.reserve("local", 2048)
        await mgr.release("local", 4096)  # release more than reserved
        assert mgr.total_reserved("local") == 0

    @pytest.mark.asyncio
    async def test_release_zero_vram_noop(self):
        """Release with 0 VRAM is a no-op."""
        mgr = VramReservationManager(vram_probe=lambda: (8192, 16384))
        await mgr.reserve("local", 4096)
        await mgr.release("local", 0)
        assert mgr.total_reserved("local") == 4096

    @pytest.mark.asyncio
    async def test_reserve_accounts_for_existing_reservations(self):
        """Multiple reservations correctly account for cumulative usage."""
        mgr = VramReservationManager(vram_probe=lambda: (8192, 16384))
        assert await mgr.reserve("local", 4096) is True
        # 4096 free remaining — another 4096 should succeed, 5000 should fail
        assert await mgr.reserve("local", 4096) is True
        assert mgr.total_reserved("local") == 8192
        assert mgr.available("local") == 0
        assert await mgr.reserve("local", 1) is False  # no VRAM left

    @pytest.mark.asyncio
    async def test_concurrent_reservations_are_atomic(self):
        """Concurrent reserve calls don't over-commit VRAM."""
        mgr = VramReservationManager(vram_probe=lambda: (4096, 16384))

        results = []
        async def contender(name):
            ok = await mgr.reserve("local", 4096)
            results.append((name, ok))

        # Run two concurrent 4096 MiB reservations — only one should succeed
        await asyncio.gather(contender("a"), contender("b"))
        successes = [name for name, ok in results if ok]
        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
        assert mgr.total_reserved("local") == 4096

    @pytest.mark.asyncio
    async def test_multiple_resource_keys_independent(self):
        """Different resource keys have independent reservation pools."""
        mgr = VramReservationManager(vram_probe=lambda: (8192, 16384))
        await mgr.reserve("local", 4096)
        await mgr.reserve("gpu-1", 2048)
        assert mgr.total_reserved("local") == 4096
        assert mgr.total_reserved("gpu-1") == 2048
        # available() uses the probe for all keys
        assert mgr.available("local") == 4096

    def test_stats_reflects_state(self):
        """stats() returns current reservation state."""
        mgr = VramReservationManager(vram_probe=lambda: (6144, 16384))
        s = mgr.stats()
        assert s["free_vram_mb"] == 6144
        assert s["total_vram_mb"] == 16384
        assert s["reserved_by_resource"] == {}


# ── GpuArbiter integration tests ────────────────────────────────────────

class TestGpuArbiterVramIntegration:
    """Tests that the GpuArbiter correctly reserves and releases VRAM."""

    @pytest.mark.asyncio
    async def test_submit_gpu_reserves_and_releases_vram(self):
        """submit_gpu reserves VRAM before running and releases after."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 16384),
            max_queue_size=10,
        )
        task = _make_task("t1", vram_mb=4096)

        # Before submission, nothing reserved
        assert arbiter._vram_reservations.total_reserved("local") == 0

        result = await arbiter.submit_gpu(task, required_vram_mb=4096)
        # The payload (asyncio.sleep(0)) returns None, which is a valid result.

        # After completion, reservation is released
        assert arbiter._vram_reservations.total_reserved("local") == 0

    @pytest.mark.asyncio
    async def test_run_gpu_task_fails_when_vram_exhausted(self):
        """_run_gpu_task raises NoResourceAvailableError when VRAM reservation fails."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (1024, 16384),
            max_queue_size=10,
        )
        task = _make_task("t1", vram_mb=4096)

        with pytest.raises(NoResourceAvailableError) as exc:
            await arbiter._run_gpu_task(task, required_vram_mb=4096, evictable=False, resource_id=None)
        assert "VRAM reservation failed" in str(exc.value)
        assert arbiter._vram_reservations.total_reserved("local") == 0

    @pytest.mark.asyncio
    async def test_submit_gpu_drops_when_queue_full(self):
        """submit_gpu raises NoResourceAvailableError when queue is full."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (1024, 16384),  # insufficient VRAM → always queue
            max_queue_size=1,
        )
        # Fill the queue with one task
        task_a = _make_task("a", vram_mb=4096)
        # submit_gpu queues task_a (blocks on _arbiter_future), so run in background
        coro_a = asyncio.create_task(arbiter.submit_gpu(task_a, required_vram_mb=4096))
        await asyncio.sleep(0.1)  # let it queue

        # Queue is now full — next task should be dropped
        task_b = _make_task("b", vram_mb=4096)
        with pytest.raises(NoResourceAvailableError) as exc:
            await arbiter.submit_gpu(task_b, required_vram_mb=4096)
        assert "queue full" in str(exc.value)

        coro_a.cancel()
        try:
            await coro_a
        except (asyncio.CancelledError, NoResourceAvailableError):
            pass

    @pytest.mark.asyncio
    async def test_submit_gpu_zero_vram_skips_reservation(self):
        """Tasks with 0 required VRAM don't touch the reservation manager."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 16384),
            max_queue_size=10,
        )
        task = _make_task("t1", vram_mb=0)
        await arbiter.submit_gpu(task, required_vram_mb=0)
        # No reservation was made or released
        assert arbiter._vram_reservations.total_reserved("local") == 0

    @pytest.mark.asyncio
    async def test_eviction_releases_local_vram(self):
        """Evicting a local GPU task releases its VRAM reservation."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 16384),
            max_queue_size=10,
        )

        started = asyncio.Event()
        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)

        task = _make_task("t-evict", vram_mb=4096)
        task.payload = blocking

        async def submitter():
            try:
                await arbiter.submit_gpu(task, required_vram_mb=4096)
            except asyncio.CancelledError:
                pass

        submit_coro = asyncio.create_task(submitter())
        await asyncio.wait_for(started.wait(), timeout=5)

        # VRAM should be reserved while the task is running
        assert arbiter._vram_reservations.total_reserved("local") == 4096

        # Evict
        arbiter._evict_task("t-evict")
        await submit_coro

        # Give the background release task time to run
        await asyncio.sleep(0.1)

        # VRAM should be released
        assert arbiter._vram_reservations.total_reserved("local") == 0

    @pytest.mark.asyncio
    async def test_concurrent_tasks_account_correctly(self):
        """Two concurrent tasks correctly reserve and release independent VRAM."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 16384),
            max_queue_size=10,
        )

        started_a = asyncio.Event()
        done_a = asyncio.Event()
        started_b = asyncio.Event()
        done_b = asyncio.Event()

        async def payload_a(_resource):
            started_a.set()
            await done_a.wait()

        async def payload_b(_resource):
            started_b.set()
            await done_b.wait()

        task_a = _make_task("a", vram_mb=2048)
        task_a.payload = payload_a
        task_b = _make_task("b", vram_mb=2048)
        task_b.payload = payload_b

        # Submit both concurrently using _run_gpu_task directly (bypasses queue)
        async def run_a():
            try:
                return await arbiter._run_gpu_task(task_a, 2048, False, None)
            except asyncio.CancelledError:
                pass

        async def run_b():
            try:
                return await arbiter._run_gpu_task(task_b, 2048, False, None)
            except asyncio.CancelledError:
                pass

        coro_a = asyncio.create_task(run_a())
        coro_b = asyncio.create_task(run_b())

        await asyncio.wait_for(started_a.wait(), timeout=5)
        await asyncio.wait_for(started_b.wait(), timeout=5)

        # Both should have reserved VRAM
        reserved = arbiter._vram_reservations.total_reserved("local")
        assert reserved == 4096, f"Expected 4096 reserved, got {reserved}"

        # Complete task A
        done_a.set()
        await coro_a
        await asyncio.sleep(0.05)
        assert arbiter._vram_reservations.total_reserved("local") == 2048

        # Complete task B
        done_b.set()
        await coro_b
        await asyncio.sleep(0.05)
        assert arbiter._vram_reservations.total_reserved("local") == 0

    @pytest.mark.asyncio
    async def test_stats_includes_vram_reservation_data(self):
        """stats() includes the vram key with reservation data."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 16384),
            max_queue_size=10,
        )
        s = arbiter.stats()
        assert "vram" in s
        assert "free_vram_mb" in s["vram"]
        assert "total_vram_mb" in s["vram"]
        assert "reserved_by_resource" in s["vram"]

    @pytest.mark.asyncio
    async def test_available_reflects_reservations(self):
        """_check_admission sees reserved VRAM as unavailable."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 16384),
            max_queue_size=10,
        )

        # Reserve 4096 via the manager
        await arbiter._vram_reservations.reserve("local", 4096)

        task = _make_task("t1", vram_mb=5000)
        admission = arbiter._check_admission(task, 5000)
        # 8192 - 4096 = 4096 available, need 5000 -> should fail
        assert admission.admitted is False
        assert admission.reason is not None and "reserved" in admission.reason

        # Clean up
        await arbiter._vram_reservations.release("local", 4096)

    @pytest.mark.asyncio
    async def test_queue_and_drain_with_vram_reservation(self):
        """Queued tasks wait for VRAM, then reserve and release on drain."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (4096, 16384),
            max_queue_size=10,
        )

        # First task consumes all VRAM
        hold = asyncio.Event()
        release_hold = asyncio.Event()

        async def long_running(_resource):
            hold.set()
            await release_hold.wait()

        async def quick(_resource):
            pass

        task_hold = _make_task("hold", vram_mb=4096)
        task_hold.payload = long_running
        task_quick = _make_task("quick", vram_mb=4096)
        task_quick.payload = quick

        # Submit the long-running task (consumes all VRAM)
        coro_hold = asyncio.create_task(arbiter.submit_gpu(task_hold, required_vram_mb=4096))
        await asyncio.wait_for(hold.wait(), timeout=5)

        # Submit the quick task — should be queued (no VRAM)
        coro_quick = asyncio.create_task(arbiter.submit_gpu(task_quick, required_vram_mb=4096))
        await asyncio.sleep(0.2)  # let it queue

        # VRAM should still be reserved for the hold task
        assert arbiter._vram_reservations.total_reserved("local") == 4096
        assert arbiter._queue.qsize() == 1  # quick task is queued

        # Release the hold task
        release_hold.set()
        await coro_hold

        # Start the queue processor to drain
        await arbiter.start()
        await asyncio.sleep(0.5)  # let the drain cycle fire

        # Quick task should complete and VRAM should be released
        await asyncio.wait_for(coro_quick, timeout=5)

        # Both tasks done, VRAM released
        assert arbiter._vram_reservations.total_reserved("local") == 0

        await arbiter.stop()


# ── Lazy export test ────────────────────────────────────────────────────

def test_vram_reservation_manager_lazy_export():
    """VramReservationManager is accessible via the lazy scheduler export."""
    from tinyagentos.scheduler import VramReservationManager as V
    assert V is VramReservationManager
