"""Tests for TOCTOU VRAM reservation fix (taOS #894 review feedback #2).

Verifies that two concurrent admissions cannot both pass when the combined
required VRAM exceeds the available free VRAM. The reservation ledger is the
shared VramReservationManager (taOS #185): it does atomic check-and-reserve so
the second caller sees the capacity already promised to the first.
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
        _reserve_and_check must see the first's reservation in the shared
        ledger and be rejected. The manager's atomic reserve closes the
        TOCTOU gap.
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
        assert arbiter._vram.reserved_vram_mb == 5000

    @pytest.mark.asyncio
    async def test_submit_gpu_reserves_and_releases(self):
        """submit_gpu reserves VRAM on admission, releases after completion."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        task = _make_task("t-release", vram_mb=0)
        task.payload = lambda r: asyncio.sleep(0)

        assert arbiter._vram.reserved_vram_mb == 0
        assert len(arbiter._pending_reservations) == 0

        await arbiter.submit_gpu(task, required_vram_mb=2048)

        # After completion, reservation should be released.
        assert arbiter._vram.reserved_vram_mb == 0
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
        resource_id = "gpu-cuda-0:t-evict-lease"

        # Reserve against the shared ledger exactly as _reserve_and_check would
        # before _run_gpu_task is entered.
        reservation = await arbiter._vram.reserve(4096, caller="gpu-task:t-evict")
        assert reservation is not None
        arbiter._pending_reservations["t-evict"] = reservation.reservation_id
        assert arbiter._vram.reserved_vram_mb == 4096

        async def runner():
            try:
                await arbiter._run_gpu_task(task, 4096, False, resource_id)
            except asyncio.CancelledError:
                pass

        runner_task = asyncio.create_task(runner())
        await asyncio.wait_for(started.wait(), timeout=5)

        # Evict — cancels the task, awaits it, then releases the reservation.
        await arbiter._evict_task("t-evict")
        await runner_task

        # After eviction, reservation should be released.
        assert arbiter._vram.reserved_vram_mb == 0
        assert "t-evict" not in arbiter._pending_reservations

    @pytest.mark.asyncio
    async def test_reservation_subtracted_from_admission_check(self):
        """_reserve_and_check sees reduced capacity when VRAM is reserved."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        # Reserve 4000 MiB.
        admission = await arbiter._reserve_and_check("t-holder", 4000)
        assert admission.admitted
        assert admission.free_vram_mb == 8192  # effective free before this reserve
        assert arbiter._vram.reserved_vram_mb == 4000

        # Now effective free = 8192 - 4000 = 4192, so 6000 is rejected.
        admission = await arbiter._reserve_and_check("t-big", 6000)
        assert not admission.admitted
        assert admission.free_vram_mb == 4192
        assert "t-big" not in arbiter._pending_reservations

        # 4000 still fits in the remaining 4192.
        admission = await arbiter._reserve_and_check("t-fit", 4000)
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
        assert arbiter._vram.reserved_vram_mb == 5000
        assert "t1" in arbiter._pending_reservations

        # Second call — should see the reservation and fail.
        admission2 = await arbiter._reserve_and_check("t2", 5000)
        assert not admission2.admitted
        # Reservation was NOT made for failed admission.
        assert arbiter._vram.reserved_vram_mb == 5000
        assert "t2" not in arbiter._pending_reservations

        # Release t1
        arbiter._release_reservation("t1")
        assert arbiter._vram.reserved_vram_mb == 0

    @pytest.mark.asyncio
    async def test_release_reservation_idempotent(self):
        """_release_reservation is safe to call multiple times."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        reservation = await arbiter._vram.reserve(1000, caller="gpu-task:t-idem")
        arbiter._pending_reservations["t-idem"] = reservation.reservation_id

        arbiter._release_reservation("t-idem")
        assert arbiter._vram.reserved_vram_mb == 0

        # Second call — no-op, no crash.
        arbiter._release_reservation("t-idem")
        assert arbiter._vram.reserved_vram_mb == 0

        # Releasing a task that never had a reservation — no-op.
        arbiter._release_reservation("non-existent")
        assert arbiter._vram.reserved_vram_mb == 0

    @pytest.mark.asyncio
    async def test_reserved_vram_in_stats(self):
        """stats() includes reservation info from the shared ledger."""
        arbiter = GpuArbiter(
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )

        stats = await arbiter.stats()
        assert stats["reserved_vram_mb"] == 0
        assert stats["pending_reservations"] == 0

        reservation = await arbiter._vram.reserve(2048, caller="gpu-task:t-stats")
        arbiter._pending_reservations["t-stats"] = reservation.reservation_id

        stats = await arbiter.stats()
        assert stats["reserved_vram_mb"] == 2048
        assert stats["pending_reservations"] == 1


class _EpochFakeCluster:
    """Fake controller that fences renew/release by lease epoch, like ClusterManager."""

    class _Lease:
        def __init__(self, lease_id, epoch):
            self.lease_id = lease_id
            self.epoch = epoch

    def __init__(self):
        self.current_epoch = "g.1"
        self.claim_calls = []
        self.renew_epochs = []
        self.release_epochs = []

    async def claim_lease(self, resource_id, caller, ttl_seconds, required_vram_mb):
        self.claim_calls.append(resource_id)
        return self._Lease("lease-1", self.current_epoch)

    async def renew_lease(self, lease_id, ttl_seconds=30, epoch=None):
        self.renew_epochs.append(epoch)
        if epoch is not None and epoch != self.current_epoch:
            return None  # takeover: the stored lease now has a different epoch
        return self._Lease(lease_id, self.current_epoch)

    async def release_lease(self, lease_id, epoch=None):
        self.release_epochs.append(epoch)
        return True


class TestGpuArbiterLeaseEpochs:
    @pytest.mark.asyncio
    async def test_renew_presents_claim_epoch_and_takeover_stops_task(self):
        cm = _EpochFakeCluster()
        arbiter = GpuArbiter(vram_probe=lambda: (8192, 8192), cluster_manager=cm, max_queue_size=10)
        arbiter._RENEW_INTERVAL_S = 0.02
        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(30)

        task = _make_task("t-epoch", vram_mb=0)
        task.payload = blocking
        resource_id = "gpu-cuda-0:t-evict-lease"
        runner = asyncio.create_task(arbiter._run_gpu_task(task, 0, False, "gpu-cuda-0:t-epoch"))
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0.1)  # a few renews with the claim epoch
        assert cm.renew_epochs and set(cm.renew_epochs) == {"g.1"}, cm.renew_epochs
        cm.current_epoch = "g.2"  # takeover: same lease_id, new epoch
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(runner, timeout=5)
        assert len(cm.claim_calls) == 1  # never re-claimed
        assert cm.release_epochs == ["g.1"]  # release presents the epoch it holds

    @pytest.mark.asyncio
    async def test_lease_renew_happy_path_keeps_task_running(self):
        cm = _EpochFakeCluster()
        arbiter = GpuArbiter(vram_probe=lambda: (8192, 8192), cluster_manager=cm, max_queue_size=10)
        arbiter._RENEW_INTERVAL_S = 0.02

        async def short(_resource):
            for _ in range(500):
                if len(cm.renew_epochs) >= 2:
                    break
                await asyncio.sleep(0.01)
            return "done"

        task = _make_task("t-happy", vram_mb=0)
        task.payload = short
        result = await asyncio.wait_for(arbiter._run_gpu_task(task, 0, False, "gpu-cuda-0:t-happy"), timeout=5)
        assert result == "done"
        assert len(cm.renew_epochs) >= 2 and set(cm.renew_epochs) == {"g.1"}
        assert cm.release_epochs == ["g.1"]


class TestClaimWindowCancel:
    """Test that a task cancelled between claim_lease and _running registration releases the lease."""

    @pytest.mark.asyncio
    async def test_cancel_between_claim_and_register_releases_lease(self):
        """FakeClusterManager that records release_lease(lease_id, epoch) calls; replace
        `arbiter._running_lock` with an object whose `__aenter__` raises asyncio.CancelledError
        on first entry (and behaves like a normal lock afterwards, so the finally can take it);
        run the task; assert exactly one release_lease call with the claimed lease_id and its
        epoch, and that lease_id is not in arbiter._lease_epochs.
        """
        # We need a fake cluster manager that records the calls.
        class RecordingClusterManager:
            def __init__(self):
                self.claimed = None
                self.renewed = None
                self.released = []  # list of (lease_id, epoch)

            async def claim_lease(self, resource_id, caller, ttl_seconds, required_vram_mb):
                self.claimed = resource_id
                # Return a fake lease with lease_id and epoch
                return type('Lease', (), {'lease_id': 'lease-123', 'epoch': 'epoch-456'})()

            async def renew_lease(self, lease_id, ttl_seconds=300, epoch=None):
                self.renewed = (lease_id, epoch)
                return type('Lease', (), {'lease_id': lease_id, 'epoch': epoch})()

            async def release_lease(self, lease_id, epoch=None):
                self.released.append((lease_id, epoch))
                return True

            # We also need to provide the other methods that the arbiter might call.
            def get_workers(self):
                return []

            def get_leases(self):
                return []

        cm = RecordingClusterManager()
        arbiter = GpuArbiter(vram_probe=lambda: (8192, 8192), cluster_manager=cm, max_queue_size=10)

        # Create a lock that will raise CancelledError on first entry, then behave normally.
        class CancelOnceLock:
            def __init__(self):
                self._lock = asyncio.Lock()
                self._cancelled = False

            async def __aenter__(self):
                if not self._cancelled:
                    self._cancelled = True
                    raise asyncio.CancelledError()
                return await self._lock.__aenter__()

            async def __aexit__(self, exc_type, exc, tb):
                return await self._lock.__aexit__(exc_type, exc, tb)

        # Replace the arbiter's _running_lock with our CancelOnceLock
        arbiter._running_lock = CancelOnceLock()

        # Create a task that will be cancelled when trying to acquire the lock.
        task = _make_task("t-cancel-window", vram_mb=1024)
        task.payload = lambda r: asyncio.sleep(0)  # dummy payload

        # We'll run the _run_gpu_task method directly to test the run method's try block.
        # We expect it to be cancelled when trying to acquire the lock.
        with pytest.raises(asyncio.CancelledError):
            await arbiter._run_gpu_task(task, 1024, False, "gpu-cuda-0:t-cancel-window")

        # After the cancelled run, we should have exactly one release_lease call.
        assert len(cm.released) == 1, f"Expected exactly one release_lease call, got {cm.released}"
        lease_id, epoch = cm.released[0]
        assert lease_id == "lease-123", f"Expected lease_id 'lease-123', got {lease_id}"
        assert epoch == "epoch-456", f"Expected epoch 'epoch-456', got {epoch}"
        # The lease_id should have been popped from _lease_epochs
        assert "lease-123" not in arbiter._lease_epochs, f"Expected lease_id not in _lease_epochs, got {arbiter._lease_epochs}"

    @pytest.mark.asyncio
    async def test_evicted_task_does_not_double_release(self):
        """Existing eviction path still releases exactly once."""
        # We'll use a similar recording cluster manager.
        class RecordingClusterManager:
            def __init__(self):
                self.claimed = None
                self.renewed = None
                self.released = []  # list of (lease_id, epoch)

            async def claim_lease(self, resource_id, caller, ttl_seconds, required_vram_mb):
                self.claimed = resource_id
                lease = type('Lease', (), {'lease_id': 'lease-123', 'epoch': 'epoch-456'})()
                return lease

            async def renew_lease(self, lease_id, ttl_seconds=300, epoch=None):
                self.renewed = (lease_id, epoch)
                return type('Lease', (), {'lease_id': lease_id, 'epoch': epoch})()

            async def release_lease(self, lease_id, epoch=None):
                self.released.append((lease_id, epoch))
                return True

            def get_workers(self):
                return []

            def get_leases(self):
                return []

        cm = RecordingClusterManager()
        arbiter = GpuArbiter(vram_probe=lambda: (8192, 8192), cluster_manager=cm, max_queue_size=10)

        # We need to get a task into the running state, then evict it.
        # We'll adapt the existing test_reservation_released_on_eviction to also check lease release.

        started = asyncio.Event()

        async def blocking(_resource):
            started.set()
            await asyncio.sleep(60)  # long running so we can evict it

        task = _make_task("t-evict-lease", vram_mb=4096)
        task.payload = blocking
        resource_id = "gpu-cuda-0:t-evict-lease"

        # Reserve against the shared ledger exactly as _reserve_and_check would
        # before _run_gpu_task is entered.
        reservation = await arbiter._vram.reserve(4096, caller="gpu-task:t-evict-lease")
        assert reservation is not None
        arbiter._pending_reservations["t-evict-lease"] = reservation.reservation_id
        assert arbiter._vram.reserved_vram_mb == 4096

        async def runner():
            try:
                await arbiter._run_gpu_task(task, 4096, False, resource_id)
            except asyncio.CancelledError:
                pass

        runner_task = asyncio.create_task(runner())
        await asyncio.wait_for(started.wait(), timeout=5)

        # Evict — cancels the task, awaits it, then releases the reservation and lease.
        result = await arbiter._evict_task("t-evict-lease")
        assert result == 1, f"Expected _evict_task to return 1, got {result}"
        await runner_task

        # After eviction, we should have exactly one release_lease call.
        assert len(cm.released) == 1, f"Expected exactly one release_lease call, got {cm.released}"
        lease_id, epoch = cm.released[0]
        assert lease_id == "lease-123", f"Expected lease_id 'lease-123', got {lease_id}"
        assert epoch == "epoch-456", f"Expected epoch 'epoch-456', got {epoch}"
        # The lease_id should have been popped from _lease_epochs
        assert "lease-123" not in arbiter._lease_epochs, f"Expected lease_id not in _lease_epochs, got {arbiter._lease_epochs}"
        # Also, the VRAM reservation should be released.
        assert arbiter._vram.reserved_vram_mb == 0
        assert "t-evict-lease" not in arbiter._pending_reservations