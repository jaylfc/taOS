"""Tests for taOS #1992 — GPU/cluster VRAM + lease accounting correctness.

Four findings audited from beta.41 Fable: H1, M1, M2, M3.
Each test must FAIL without the corresponding fix.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch, MagicMock

import pytest

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo
from tinyagentos.scheduling.leases import LeaseManager
from tinyagentos.vram_reservation import VramReservationManager


# ── H1 helpers ───────────────────────────────────────────────────────────────

def _worker(name: str, free_vram_mb: int | None = None) -> WorkerInfo:
    """Build a WorkerInfo with the given free_vram_mb."""
    w = WorkerInfo(
        name=name,
        url=f"http://{name}:9000",
        capabilities=["llm-chat"],
        free_vram_mb=free_vram_mb,
        used_vram_mb=(16384 - free_vram_mb) if free_vram_mb is not None else None,
    )
    w.status = "online"
    w.last_heartbeat = time.time()
    return w


# ── H1: claim_lease over-commits worker VRAM ────────────────────────────────

class TestH1OvercommitVRAM:
    """Two concurrent claims on different resources but same worker should
    account for total required_vram_mb across ALL active leases."""

    @pytest.mark.asyncio
    async def test_second_claim_rejected_when_total_exceeds_free(self):
        """First claim of 6 GB against an 8 GB worker succeeds; second 6 GB
        on a different resource should FAIL because 6+6=12 > 8.

        The buggy code only checks `required_vram_mb > worker.free_vram_mb`
        per-claim (6000 > 8000 → False), so both pass — OOM risk."""
        mgr = ClusterManager()
        w = _worker("gpu-node", free_vram_mb=8000)
        w.resources = ["gpu-cuda-0", "gpu-cuda-1"]
        mgr._workers["gpu-node"] = w

        lease_1 = await mgr.claim_lease(
            resource_id="gpu-node:gpu-cuda-0",
            caller="first", ttl_seconds=30, required_vram_mb=6000,
        )
        assert lease_1 is not None, "First 6 GB claim should succeed"

        # Second claim — buggy code sees 6000 > 8000 → False and admits.
        # Fix must sum active leases' required_vram_mb for this worker.
        lease_2 = await mgr.claim_lease(
            resource_id="gpu-node:gpu-cuda-1",
            caller="second", ttl_seconds=30, required_vram_mb=6000,
        )

        assert lease_2 is None, (
            "Second 6 GB claim should FAIL: total (12 GB) > free (8 GB)"
        )


# ── M1: renew() resurrects an expired lease ─────────────────────────────────

class TestM1RenewResurrects:
    """A renewed lease whose expires_at is in the past must return None."""

    @pytest.mark.asyncio
    async def test_renew_expired_lease_returns_none(self):
        """Acquire a lease, let it expire (tiny TTL), then renew() —
        must return None. The buggy _renew_locked does UPDATE without checking
        expires_at < now."""
        lm = LeaseManager(":memory:")
        await lm.init()

        lease = await lm.acquire(
            resource_key="test:kg", agent_name="agent-a", ttl=0.01,
        )
        assert lease is not None

        # Wait for expiration
        time.sleep(0.2)

        result = await lm.renew("test:kg", "agent-a", ttl=60)

        assert result is None, (
            "renew of an expired lease must return None (M1 fix)"
        )


# ── M3: thread-unsafe reservation sweep ─────────────────────────────────────

class TestM3ThreadUnsafeSweep:
    """Concurrent available_vram() + stats() calls must not corrupt state."""

    @pytest.mark.asyncio
    async def test_concurrent_sweep_no_negative_reserved(self):
        """Multiple concurrent callers of available_vram()/stats() that sweep
        stale reservations should never leave _reserved_vram_mb negative or
        _pending corrupted."""
        # Use a probe that always reports plenty of VRAM so reserves grant.
        mgr = VramReservationManager(
            ttl_seconds=0.1, probe=lambda: (16384, 16384),
        )

        # Reserve some VRAM so we have accounting to track
        r1 = await mgr.reserve(4096, caller="pull-1")
        assert r1 is not None
        r2 = await mgr.reserve(4096, caller="pull-2")
        assert r2 is not None

        initial_reserved = mgr.reserved_vram_mb
        assert initial_reserved == 8192  # sum of reservations

        # Wait for both to become stale
        time.sleep(0.15)

        # Launch concurrent sweeps via thread — sync methods need asyncio.to_thread
        tasks = (
            [asyncio.create_task(asyncio.to_thread(mgr.available_vram))
             for _ in range(6)]
            + [asyncio.create_task(asyncio.to_thread(mgr.stats))
               for _ in range(6)]
        )

        await asyncio.gather(*tasks)

        # After sweeps, reserved should not be negative and pending should be 0
        assert mgr.reserved_vram_mb >= 0, (
            f"_reserved_vram_mb must not go negative after sweep: {mgr.reserved_vram_mb}"
        )
        assert mgr.pending_count == 0, (
            "All stale reservations should have been swept; pending_count=0"
        )


# ── M2: probe failure must not reverse the fail-open/fail-closed split ──────

class TestM2ProbeFailure:
    """`normalise_vram_probe(free_mb, total_mb)` must distinguish "probe
    unavailable" (total == 0 → fail OPEN) from "probe ran, 0 free" (total > 0,
    free == 0 → fail CLOSED). Collapsing both to 0 permanently refuses every
    estimated_memory task on non-NVIDIA hosts; collapsing both to 999_999
    re-inflates capacity on a genuinely full GPU."""

    def test_probe_unavailable_fails_open(self):
        """total <= 0 is *unknown* (no nvidia-smi) — must NOT block scheduling."""
        from tinyagentos.scheduler.discovery import normalise_vram_probe
        assert normalise_vram_probe(0, 0) == 999_999
        assert normalise_vram_probe(-1, 0) == 999_999

    def test_probe_ran_zero_free_fails_closed(self):
        """total > 0 and free <= 0 is *known full* — must refuse admission."""
        from tinyagentos.scheduler.discovery import normalise_vram_probe
        assert normalise_vram_probe(0, 8192) == 0
        assert normalise_vram_probe(-1, 8192) == 0

    def test_positive_probe_passthrough(self):
        from tinyagentos.scheduler.discovery import normalise_vram_probe
        assert normalise_vram_probe(8000, 8192) == 8000


class TestM2CanAcceptFailOpen:
    """The behaviour that actually regressed: `_gpu_vram_probe` feeding
    `Resource.can_accept()` on a host whose probe (probe fails → 0,0) cannot run.

    With the buggy all-zero collapse, every `estimated_memory_mb > 0` task is
    refused on any GPU resource whose nvidia-smi is absent. `can_accept` must
    admit the task when the probe is UNAVAILABLE (fail open), and refuse only
    when the probe RAN and measured insufficient free VRAM (fail closed)."""

    def _resource(self, memory_probe):
        from tinyagentos.scheduler.resource import Resource, Tier
        from tinyagentos.scheduler.types import ResourceSignature

        sig = ResourceSignature(
            platform="linux", runtime="native", runtime_version="",
        )
        return Resource(
            name="gpu-rocm",
            signature=sig,
            concurrency=1,
            get_capabilities=lambda: {"llm-chat"},
            backend_lookup=lambda _cap: None,
            tier=Tier.GPU,
            memory_probe=memory_probe,
        )

    @staticmethod
    def _task(estimated_memory_mb: int):
        from tinyagentos.scheduler.types import Task, Capability

        async def payload(_res):
            return None

        return Task(
            capability=Capability.LLM_CHAT,
            payload=payload,
            preferred_resources=[],
            estimated_memory_mb=estimated_memory_mb,
        )

    def test_probe_unavailable_does_not_block(self):
        """Probe returns (0,0) → normalise → 999_999 → can_admit admits."""
        from tinyagentos.scheduler.discovery import normalise_vram_probe

        def probe():
            free, total = 0, 0
            return normalise_vram_probe(free, total)

        res = self._resource(probe)
        ok, _why = res.can_admit(self._task(4096))
        assert ok, "probe-unavailable host must fail OPEN, not refuse every task"

    def test_probe_zero_free_blocks(self):
        """Probe RAN and measured 0 free → normalise → 0 → can_admit refuses."""
        from tinyagentos.scheduler.discovery import normalise_vram_probe

        def probe():
            free, total = 0, 16384
            return normalise_vram_probe(free, total)

        res = self._resource(probe)
        ok, why = res.can_admit(self._task(4096))
        assert not ok, "a genuinely full GPU must refuse a 4 GB task"
        assert why and "insufficient memory" in why


# ── H2: explicit unregister leaks a running arbiter task ────────────────────

class TestH2UnregisterCancelsArbiter:
    """``unregister_worker`` must cancel the worker's running GPU arbiter tasks.

    The admin-unregister path released the lease rows but never told the arbiter,
    so a running task survived with its VRAM reservation and ``_running`` slot
    held. A returning worker (or a fresh claimant) would then run concurrently
    with the orphan on the same physical GPU — the shared-GPU corruption class
    #1992 is about. Uses a REAL GpuArbiter + real lease/reservation, not a mock,
    so both halves of the invariant (cancelled task + freed resources) are
    actually exercised.
    """

    @pytest.mark.asyncio
    async def test_unregister_cancels_running_arbiter_task(self):
        from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
        from tinyagentos.scheduler.types import Capability, Priority, Task

        mgr = ClusterManager()
        w = _worker("gpu-box", free_vram_mb=8192)
        w.resources = ["gpu-cuda-0"]
        mgr._workers["gpu-box"] = w

        # Local probe is present so admission holds a real VRAM reservation;
        # the cluster manager lets _run_gpu_task claim a real lease.
        arbiter = GpuArbiter(
            cluster_manager=mgr,
            vram_probe=lambda: (8192, 8192),
            max_queue_size=10,
        )
        mgr._gpu_arbiter = arbiter

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def long_running(_resource):
            started.set()
            try:
                await asyncio.sleep(60)  # never finishes on its own
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = Task(
            id="t-unreg",
            capability=Capability.LLM_CHAT,
            payload=long_running,
            preferred_resources=[],
            priority=Priority.INTERACTIVE_AGENT,
            estimated_vram_mb=1024,
        )

        async def submitter():
            try:
                await arbiter.submit_gpu(
                    task, required_vram_mb=1024,
                    resource_id="gpu-box:gpu-cuda-0",
                )
            except asyncio.CancelledError:
                return "cancelled"
            return "completed"

        submit_coro = asyncio.create_task(submitter())
        await asyncio.wait_for(started.wait(), timeout=5)

        # Preconditions: a live lease, a running slot, and a VRAM reservation.
        assert len(mgr.get_leases()) == 1
        assert "t-unreg" in arbiter._running
        assert "t-unreg" in arbiter._pending_reservations
        assert arbiter._vram.reserved_vram_mb == 1024

        assert await mgr.unregister_worker("gpu-box") is True

        # (a) the arbiter task is cancelled. Bounded wait (no wait_for, which
        # would cancel the very coroutine under test and fake a pass).
        done, _pending = await asyncio.wait({submit_coro}, timeout=1)
        assert done, "unregister did not cancel the running arbiter task"
        assert submit_coro.result() == "cancelled"
        assert cancelled.is_set(), "running GPU payload was not cancelled"
        assert "t-unreg" not in arbiter._running
        assert "t-unreg" not in arbiter._running_tasks
        # (b) ... and its VRAM reservation is freed.
        assert "t-unreg" not in arbiter._pending_reservations
        assert arbiter._vram.reserved_vram_mb == 0
        # The lease rows are gone too (the pre-existing half of the behaviour).
        assert mgr.get_leases() == []
