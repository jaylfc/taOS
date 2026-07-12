"""Tests for atomic VRAM reservation (taOS #1706 TOCTOU fix / #1766 audit).

Verifies that:
- Concurrent reserve calls cannot over-commit VRAM.
- reserve() returns None when there's not enough VRAM.
- release() correctly returns VRAM to the pool.
- release() is idempotent.
- Zero-VRAM reservations always succeed.
- stats() reflects the current state.
- No-probe hardware fails OPEN with bookkeeping (#1766 HIGH).
- Stale reservations past TTL are reclaimed (#1766 LOW).
"""

import asyncio
import time

import pytest

from tinyagentos.vram_reservation import (
    DEFAULT_RESERVATION_TTL_SECONDS,
    VramReservationManager,
)


# ── test helpers ────────────────────────────────────────────────────


def _manager_with_probe(
    free_mb: int,
    total_mb: int,
    ttl_seconds: float = DEFAULT_RESERVATION_TTL_SECONDS,
) -> VramReservationManager:
    """Return a manager whose _probe_vram returns fixed values."""
    mgr = VramReservationManager(ttl_seconds=ttl_seconds)

    def _fake_probe() -> tuple[int, int]:
        return free_mb, total_mb

    mgr._probe_vram = staticmethod(_fake_probe)  # type: ignore[method-assign]
    return mgr


# ── basic reserve / release ─────────────────────────────────────────


class TestReserveRelease:
    """Happy-path reserve and release."""

    @pytest.mark.asyncio
    async def test_reserve_when_vram_available(self):
        """reserve() succeeds when enough VRAM is free."""
        mgr = _manager_with_probe(8192, 8192)
        reservation = await mgr.reserve(4096, caller="test")
        assert reservation is not None
        assert reservation.vram_mb == 4096
        assert reservation.caller == "test"
        assert mgr.reserved_vram_mb == 4096
        assert mgr.pending_count == 1

    @pytest.mark.asyncio
    async def test_reserve_denied_when_insufficient(self):
        """reserve() returns None when VRAM is too low."""
        mgr = _manager_with_probe(2048, 8192)
        reservation = await mgr.reserve(4096, caller="test")
        assert reservation is None
        assert mgr.reserved_vram_mb == 0
        assert mgr.pending_count == 0

    @pytest.mark.asyncio
    async def test_release_returns_vram(self):
        """release() returns reserved VRAM to the pool."""
        mgr = _manager_with_probe(8192, 8192)
        res = await mgr.reserve(4096, caller="test")
        assert res is not None

        released = mgr.release(res.reservation_id)
        assert released is True
        assert mgr.reserved_vram_mb == 0
        assert mgr.pending_count == 0

    @pytest.mark.asyncio
    async def test_release_idempotent(self):
        """release() is safe to call multiple times."""
        mgr = _manager_with_probe(8192, 8192)
        res = await mgr.reserve(4096, caller="test")
        assert res is not None

        assert mgr.release(res.reservation_id) is True
        assert mgr.release(res.reservation_id) is False  # already released
        assert mgr.release("non-existent") is False
        assert mgr.reserved_vram_mb == 0

    @pytest.mark.asyncio
    async def test_multiple_reservations_serial(self):
        """Serial reservations accumulate correctly."""
        mgr = _manager_with_probe(8192, 8192)

        r1 = await mgr.reserve(2000, caller="a")
        r2 = await mgr.reserve(3000, caller="b")
        assert r1 is not None
        assert r2 is not None
        assert mgr.reserved_vram_mb == 5000
        assert mgr.pending_count == 2

        mgr.release(r1.reservation_id)
        assert mgr.reserved_vram_mb == 3000

        mgr.release(r2.reservation_id)
        assert mgr.reserved_vram_mb == 0

    @pytest.mark.asyncio
    async def test_reserve_at_boundary(self):
        """Reserving exactly the free amount succeeds."""
        mgr = _manager_with_probe(4096, 8192)
        res = await mgr.reserve(4096, caller="test")
        assert res is not None
        assert mgr.reserved_vram_mb == 4096

    @pytest.mark.asyncio
    async def test_reserve_one_more_than_free_fails(self):
        """Reserving free+1 fails."""
        mgr = _manager_with_probe(4096, 8192)
        res = await mgr.reserve(4097, caller="test")
        assert res is None


# ── concurrent safety ───────────────────────────────────────────────


class TestConcurrentSafety:
    """Two concurrent reserve calls cannot over-commit."""

    @pytest.mark.asyncio
    async def test_concurrent_reserve_only_one_admitted(self):
        """With 8192 MiB free and each needing 5000 MiB, only one admitted."""
        mgr = _manager_with_probe(8192, 8192)
        results: list[bool] = []

        async def try_reserve(caller: str):
            res = await mgr.reserve(5000, caller=caller)
            results.append(res is not None)

        await asyncio.gather(try_reserve("a"), try_reserve("b"))

        admitted = sum(results)
        assert admitted == 1, f"Expected 1 admission, got {admitted}: {results}"
        assert mgr.reserved_vram_mb == 5000

    @pytest.mark.asyncio
    async def test_concurrent_vram_accounting(self):
        """After two serial reserves filling VRAM, a third fails."""
        mgr = _manager_with_probe(8192, 8192)

        r1 = await mgr.reserve(4000, caller="a")
        r2 = await mgr.reserve(4000, caller="b")
        assert r1 is not None
        assert r2 is not None

        # Third should fail — only 192 MiB effective left.
        r3 = await mgr.reserve(4000, caller="c")
        assert r3 is None
        assert mgr.reserved_vram_mb == 8000


# ── zero / negative VRAM ────────────────────────────────────────────


class TestZeroVram:
    """Zero-VRAM reservations always succeed as lightweight markers."""

    @pytest.mark.asyncio
    async def test_zero_vram_reservation(self):
        mgr = _manager_with_probe(0, 0)
        res = await mgr.reserve(0, caller="noop")
        assert res is not None
        assert res.vram_mb == 0
        assert mgr.reserved_vram_mb == 0
        assert mgr.pending_count == 0

    @pytest.mark.asyncio
    async def test_negative_vram_reservation(self):
        mgr = _manager_with_probe(8192, 8192)
        res = await mgr.reserve(-1, caller="test")
        assert res is not None
        assert res.vram_mb == 0
        assert mgr.reserved_vram_mb == 0
        assert mgr.pending_count == 0


# ── available_vram / stats ──────────────────────────────────────────


class TestAvailableVram:
    """available_vram() and stats() reflect current state."""

    @pytest.mark.asyncio
    async def test_available_vram_no_reservations(self):
        mgr = _manager_with_probe(8192, 16384)
        free, total = mgr.available_vram()
        assert free == 8192
        assert total == 16384

    @pytest.mark.asyncio
    async def test_available_vram_with_reservations(self):
        mgr = _manager_with_probe(8192, 16384)
        res = await mgr.reserve(3000, caller="test")
        assert res is not None

        free, total = mgr.available_vram()
        assert free == 5192  # 8192 - 3000
        assert total == 16384

    @pytest.mark.asyncio
    async def test_stats(self):
        mgr = _manager_with_probe(8192, 16384)

        s = mgr.stats()
        assert s["free_vram_mb"] == 8192
        assert s["total_vram_mb"] == 16384
        assert s["reserved_vram_mb"] == 0
        assert s["effective_free_mb"] == 8192
        assert s["pending_reservations"] == 0

        res = await mgr.reserve(4096, caller="test")
        assert res is not None

        s = mgr.stats()
        assert s["reserved_vram_mb"] == 4096
        assert s["effective_free_mb"] == 4096
        assert s["pending_reservations"] == 1


# ── real probe (integration smoke test) ─────────────────────────────


class TestRealProbe:
    """Smoke-test with the real nvidia-smi probe (if available)."""

    @pytest.mark.asyncio
    async def test_reserve_with_real_probe(self):
        """Reserving 1 MiB should always succeed on a real GPU, and on
        no-probe hardware fail-open also admits (bookkeeping only)."""
        mgr = VramReservationManager()
        res = await mgr.reserve(1, caller="smoke-test")
        # With a real GPU and free VRAM this succeeds. Without nvidia-smi the
        # probe returns None and reserve() fails open, so this also succeeds.
        assert res is not None
        mgr.release(res.reservation_id)


# ── no-probe hardware (AMD / Apple / Rockchip): fail open ────────────


def _manager_without_probe(
    ttl_seconds: float = 3600.0,
) -> VramReservationManager:
    """Return a manager whose probe reports no VRAM visibility (None)."""
    mgr = VramReservationManager(ttl_seconds=ttl_seconds)
    mgr._probe_vram = staticmethod(lambda: None)  # type: ignore[method-assign]
    return mgr


class TestNoProbeFailOpen:
    """Without a VRAM probe we cannot prove insufficiency, so admission
    fails OPEN (bookkeeping only), matching cluster claim_lease semantics.
    A closed fail here 503s every model pull on non-NVIDIA hosts."""

    @pytest.mark.asyncio
    async def test_reserve_admits_without_probe(self):
        mgr = _manager_without_probe()
        res = await mgr.reserve(4096, caller="rkllama-pull:test")
        assert res is not None
        assert res.vram_mb == 4096
        # Bookkeeping still recorded so a future probe-aware path sees it.
        assert mgr.reserved_vram_mb == 4096
        assert mgr.pending_count == 1
        mgr.release(res.reservation_id)
        assert mgr.reserved_vram_mb == 0

    @pytest.mark.asyncio
    async def test_no_probe_real_backend_min_ram_proceeds(self):
        """Acceptance (#1766): on a box with no nvidia-smi, a pull with a
        real backend-level min_ram_mb proceeds with bookkeeping and no deny.
        """
        mgr = _manager_without_probe()
        # 2048 is the catalog figure for qwen2.5-1.5b-rkllm backends.min_ram_mb
        res = await mgr.reserve(2048, caller="rkllama-pull:qwen2.5-1.5b-rkllm")
        assert res is not None
        assert res.vram_mb == 2048
        assert mgr.reserved_vram_mb == 2048
        assert mgr.pending_count == 1

    @pytest.mark.asyncio
    async def test_stats_reports_probe_unavailable(self):
        mgr = _manager_without_probe()
        s = mgr.stats()
        assert s["probe_available"] is False
        assert s["free_vram_mb"] == 0 and s["total_vram_mb"] == 0
        assert "ttl_seconds" in s

    @pytest.mark.asyncio
    async def test_probe_present_still_denies_real_insufficiency(self):
        # Regression guard: fail-open applies ONLY to the no-probe case; a
        # real probe showing insufficient VRAM must still deny.
        mgr = _manager_with_probe(free_mb=1024, total_mb=8192)
        res = await mgr.reserve(4096, caller="too-big")
        assert res is None


# ── concurrent rkllm-style atomic check on NVIDIA ────────────────────


class TestConcurrentRkllmStyle:
    """Acceptance (#1766): two concurrent large-model pulls on NVIDIA still
    hit the atomic check (only one admitted when free VRAM cannot fit both).
    """

    @pytest.mark.asyncio
    async def test_two_concurrent_large_reserves_only_one_admitted(self):
        # 8192 free, each "rkllm" pull wants 5000 (like a large model).
        mgr = _manager_with_probe(8192, 8192)
        results: list[bool] = []

        async def try_reserve(caller: str):
            res = await mgr.reserve(5000, caller=caller)
            results.append(res is not None)

        await asyncio.gather(
            try_reserve("rkllama-pull:model-a"),
            try_reserve("rkllama-pull:model-a"),
        )

        assert sum(results) == 1
        assert mgr.reserved_vram_mb == 5000
        assert mgr.pending_count == 1


# ── TTL sweep for hung installers ────────────────────────────────────


class TestTtlSweep:
    """Acceptance (#1766): a reservation older than its TTL is reclaimed."""

    @pytest.mark.asyncio
    async def test_sweep_reclaims_stale_reservation(self):
        mgr = _manager_with_probe(8192, 8192)
        mgr._ttl_seconds = 60.0
        res = await mgr.reserve(4000, caller="hung-installer")
        assert res is not None
        assert mgr.reserved_vram_mb == 4000

        # Age the reservation past the TTL.
        res.created_at = time.time() - 120.0
        reclaimed = mgr.sweep_stale()
        assert reclaimed == 1
        assert mgr.reserved_vram_mb == 0
        assert mgr.pending_count == 0
        # Idempotent: already released id is unknown.
        assert mgr.release(res.reservation_id) is False

    @pytest.mark.asyncio
    async def test_fresh_reservation_not_reclaimed(self):
        mgr = _manager_with_probe(8192, 8192)
        mgr._ttl_seconds = 3600.0
        res = await mgr.reserve(2000, caller="active")
        assert res is not None
        assert mgr.sweep_stale() == 0
        assert mgr.pending_count == 1
        assert mgr.reserved_vram_mb == 2000

    @pytest.mark.asyncio
    async def test_reserve_sweeps_before_admission(self):
        """Stale holds must not permanently starve a later pull."""
        mgr = _manager_with_probe(5000, 8192)
        mgr._ttl_seconds = 30.0
        stuck = await mgr.reserve(4000, caller="hung")
        assert stuck is not None
        stuck.created_at = time.time() - 120.0

        # Without sweep this would deny (5000 free - 4000 reserved < 4000).
        # With sweep the stale hold is reclaimed first.
        next_res = await mgr.reserve(4000, caller="fresh")
        assert next_res is not None
        assert mgr.reserved_vram_mb == 4000
        assert mgr.pending_count == 1

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_expiry(self):
        mgr = VramReservationManager(ttl_seconds=0)
        mgr._probe_vram = staticmethod(lambda: (8192, 8192))  # type: ignore[method-assign]
        res = await mgr.reserve(1000, caller="permanent")
        assert res is not None
        res.created_at = time.time() - 10_000.0
        assert mgr.sweep_stale() == 0
        assert mgr.pending_count == 1

    @pytest.mark.asyncio
    async def test_available_vram_sweeps_stale(self):
        mgr = _manager_with_probe(8192, 8192)
        mgr._ttl_seconds = 10.0
        res = await mgr.reserve(3000, caller="old")
        assert res is not None
        res.created_at = time.time() - 60.0
        free, total = mgr.available_vram()
        assert free == 8192  # stale hold reclaimed
        assert total == 8192
        assert mgr.pending_count == 0