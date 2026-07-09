"""Tests for atomic VRAM reservation (taOS #1706 TOCTOU fix).

Verifies that:
- Concurrent reserve calls cannot over-commit VRAM.
- reserve() returns None when there's not enough VRAM.
- release() correctly returns VRAM to the pool.
- release() is idempotent.
- Zero-VRAM reservations always succeed.
- stats() reflects the current state.
"""

import asyncio

import pytest

from tinyagentos.vram_reservation import VramReservationManager


# ── test helpers ────────────────────────────────────────────────────


def _manager_with_probe(free_mb: int, total_mb: int) -> VramReservationManager:
    """Return a manager whose _probe_vram returns fixed values."""
    mgr = VramReservationManager()

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
        """Reserving 1 MiB should always succeed on a real GPU."""
        mgr = VramReservationManager()
        res = await mgr.reserve(1, caller="smoke-test")
        # On a system with nvidia-smi and some free VRAM, this should succeed.
        # On a system without nvidia-smi, the probe returns (0, 0) so
        # this will fail — that's fine, the test is a best-effort smoke.
        if res is not None:
            mgr.release(res.reservation_id)


# ── no-probe hardware (AMD / Apple / Rockchip): fail open ────────────


def _manager_without_probe() -> VramReservationManager:
    """Return a manager whose probe reports no VRAM visibility (None)."""
    mgr = VramReservationManager()
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
    async def test_stats_reports_probe_unavailable(self):
        mgr = _manager_without_probe()
        s = mgr.stats()
        assert s["probe_available"] is False
        assert s["free_vram_mb"] == 0 and s["total_vram_mb"] == 0

    @pytest.mark.asyncio
    async def test_probe_present_still_denies_real_insufficiency(self):
        # Regression guard: fail-open applies ONLY to the no-probe case; a
        # real probe showing insufficient VRAM must still deny.
        mgr = _manager_with_probe(free_mb=1024, total_mb=8192)
        res = await mgr.reserve(4096, caller="too-big")
        assert res is None
