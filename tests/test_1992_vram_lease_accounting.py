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


# ── M2: probe failure → 999_999 — test the fail-closed normaliser directly ───

class TestM2ProbeFailureFakeVRAM:
    """When the nvidia-smi probe fails or returns zero, discovery must NOT
    inflate capacity to 999_999. The fail-closed rule is `normalise_vram_probe`
    (a module-level helper so the behaviour is unit-testable)."""

    def test_normalise_positive_probe_passthrough(self):
        """A real positive probe value is passed through unchanged."""
        from tinyagentos.scheduler.discovery import normalise_vram_probe
        assert normalise_vram_probe(8000) == 8000

    def test_normalise_zero_probe_fails_closed(self):
        """A zero probe must map to 0, not an optimistic huge value."""
        from tinyagentos.scheduler.discovery import normalise_vram_probe
        assert normalise_vram_probe(0) == 0

    def test_normalise_negative_probe_fails_closed(self):
        """A negative (failed) probe must map to 0, not 999_999."""
        from tinyagentos.scheduler.discovery import normalise_vram_probe
        assert normalise_vram_probe(-1) == 0
        # Explicitly assert the old buggy value is never produced.
        assert normalise_vram_probe(-1) != 999_999
