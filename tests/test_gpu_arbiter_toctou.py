"""Tests for TOCTOU VRAM reservation fix (taOS #894 review feedback #2).

Verifies that two concurrent admissions cannot both pass when the combined
required VRAM exceeds the available free VRAM.  The fix subtracts in-flight
reservations from the nvidia-smi probe so the second caller sees the
capacity already promised to the first.
"""
import asyncio

import pytest

from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task


def _make_task(task_id="t1", vram_mb=0, priority=Priority.INTERACTIVE_AGENT):
    return Task(
        id=task_id, capability=Capability.LLM_CHAT,
        payload=lambda r: asyncio.sleep(0),
        preferred_resources=[], priority=priority,
        estimated_vram_mb=vram_mb,
    )


class TestToctouReservation:
    """Concurrent admission doesn't over-commit VRAM."""

    @pytest.mark.asyncio
    async def test_concurrent_reserve_and_check_only_one_admitted(self):
        """Two concurrent _reserve_and_check calls — only first admitted.

        With 8192 MiB free and each needing 5000 MiB, the second
        _reserve_and_check must see the first's reservation and be
        rejected.  The atomic lock ensures no TOCTOU gap.
        """
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        results: list[bool] = []

        async def try_reserve(task_id):
            admission = await arbiter._reserve_and_check(task_id, 5000)
            results.append(admission.admitted)

        # Launch both concurrently — this is the TOCTOU window.
        await asyncio.gather(
            try_reserve("t-a"),
            try_reserve("t-b"),
        )

        admitted_count = sum(results)
        assert admitted_count == 1, (
            f"Expected exactly 1 admission, got {admitted_count}: {results}"
        )
        assert arbiter._reserved_vram_mb == 5000

    @pytest.mark.asyncio
    async def test_submit_gpu_reserves_and_releases(self):
        """submit_gpu reserves VRAM on admission, releases after completion."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        task = _make_task("t-release", vram_mb=0)
        task.payload = lambda r: asyncio.sleep(0)

        assert arbiter._reserved_vram_mb == 0
        assert len(arbiter._pending_reservations) == 0

        await arbiter.submit_gpu(task, required_vram_mb=2048)

        # After completion, reservation should be released.
        assert arbiter._reserved_vram_mb == 0
        assert len(arbiter._pending_reservations) == 0

    @pytest.mark.asyncio
    async def test_reservation_released_on_eviction(self):
        """Evicting a running task releases its VRAM reservation."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)

        task = _make_task("t-evict", vram_mb=0)
        task.payload = blocking

        async def runner():
            try:
                await arbiter._run_gpu_task(task, 4096, False, None)
            except asyncio.CancelledError:
                pass

        runner_task = asyncio.create_task(runner())
        await asyncio.wait_for(started.wait(), timeout=5)

        # Simulate what _reserve_and_check would have done when the task
        # was admitted.
        arbiter._reserved_vram_mb += 4096
        arbiter._pending_reservations["t-evict"] = 4096
        assert arbiter._reserved_vram_mb == 4096

        # Evict
        await arbiter._evict_task("t-evict")
        await runner_task

        # After eviction, reservation should be released.
        assert arbiter._reserved_vram_mb == 0
        assert "t-evict" not in arbiter._pending_reservations

    @pytest.mark.asyncio
    async def test_reservation_subtracted_from_admission_check(self):
        """_check_admission sees reduced capacity when VRAM is reserved."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        # Initially 8192 free — 6000 should be okay.
        admission = arbiter._check_admission(None, 6000)
        assert admission.admitted
        assert admission.free_vram_mb == 8192  # effective free (0 reserved)

        # Reserve 4000 MiB.
        arbiter._reserved_vram_mb = 4000
        arbiter._pending_reservations["t-holder"] = 4000

        # Now effective free = 8192 - 4000 = 4192.
        # 6000 should be rejected.
        admission = arbiter._check_admission(None, 6000)
        assert not admission.admitted
        assert admission.free_vram_mb == 4192

        # 4000 should still be admitted.
        admission = arbiter._check_admission(None, 4000)
        assert admission.admitted

    @pytest.mark.asyncio
    async def test_atomic_reserve_and_check(self):
        """_reserve_and_check atomically checks and reserves."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        # First call — should admit and reserve.
        admission = await arbiter._reserve_and_check("t1", 5000)
        assert admission.admitted
        assert arbiter._reserved_vram_mb == 5000
        assert arbiter._pending_reservations == {"t1": 5000}

        # Second call — should see the reservation and fail.
        admission2 = await arbiter._reserve_and_check("t2", 5000)
        assert not admission2.admitted
        # Reservation was NOT made for failed admission.
        assert arbiter._reserved_vram_mb == 5000
        assert "t2" not in arbiter._pending_reservations

        # Release t1
        arbiter._release_reservation("t1")
        assert arbiter._reserved_vram_mb == 0

    @pytest.mark.asyncio
    async def test_release_reservation_idempotent(self):
        """_release_reservation is safe to call multiple times."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        arbiter._reserved_vram_mb = 1000
        arbiter._pending_reservations["t-idem"] = 1000

        arbiter._release_reservation("t-idem")
        assert arbiter._reserved_vram_mb == 0

        # Second call — no-op, no crash.
        arbiter._release_reservation("t-idem")
        assert arbiter._reserved_vram_mb == 0

        # Releasing a task that never had a reservation — no-op.
        arbiter._release_reservation("non-existent")
        assert arbiter._reserved_vram_mb == 0

    @pytest.mark.asyncio
    async def test_reserved_vram_in_stats(self):
        """stats() includes reservation info."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        stats = arbiter.stats()
        assert stats["reserved_vram_mb"] == 0
        assert stats["pending_reservations"] == 0

        arbiter._reserved_vram_mb = 2048
        arbiter._pending_reservations["t-stats"] = 2048

        stats = arbiter.stats()
        assert stats["reserved_vram_mb"] == 2048
        assert stats["pending_reservations"] == 1
