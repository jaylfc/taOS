"""
Atomic VRAM reservation — check-and-reserve to prevent TOCTOU races.

When two concurrent model loads both check available VRAM before either
consumes it, they can both pass and cause OOM.  This module provides
atomic check-and-reserve semantics: check available VRAM, reserve what
you need, then load the model.  Concurrent callers see the reserved
capacity and back off.

Pattern:

    mgr = VramReservationManager()
    reservation = await mgr.reserve(vram_mb=4096, caller="ollama-pull")
    if reservation is not None:
        try:
            await load_model()
        finally:
            mgr.release(reservation.reservation_id)
    else:
        raise RuntimeError("Not enough VRAM")

The reservation is *not* a context manager — callers must release
explicitly (typically in a ``finally`` block) so the reservation can
outlive a single ``async with`` block while the model loads.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VramReservation:
    """A held VRAM reservation.  Release via manager.release()."""

    reservation_id: str
    vram_mb: int
    caller: str
    created_at: float


class VramReservationManager:
    """Atomic VRAM check-and-reserve for model loads.

    Thread-safe via ``asyncio.Lock``.  Reads real-time VRAM from
    nvidia-smi and subtracts in-flight reservations so concurrent
    callers see the capacity already promised to others.

    Zero-VRAM reservations (``vram_mb <= 0``) always succeed as a
    lightweight marker — no lock acquisition, no probe, no accounting.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._reserved_vram_mb: int = 0
        self._pending: dict[str, VramReservation] = {}

    # ── public API ──────────────────────────────────────────────────

    async def reserve(
        self,
        vram_mb: int,
        caller: str = "",
    ) -> VramReservation | None:
        """Atomically check available VRAM and reserve *vram_mb* MiB.

        Returns a ``VramReservation`` if admitted, ``None`` if
        insufficient VRAM.  Caller **must** call :meth:`release` when
        done — this object is not a context manager so the reservation
        can outlive a single ``async with`` block while the model loads.

        *vram_mb* ≤ 0 always succeeds without probing VRAM or acquiring
        the lock (a lightweight no-op marker).
        """
        if vram_mb <= 0:
            return VramReservation(
                reservation_id=f"vr_{secrets.token_hex(4)}",
                vram_mb=0,
                caller=caller or "noop",
                created_at=time.time(),
            )

        async with self._lock:
            # Probe in a thread: nvidia-smi is a blocking subprocess and this
            # runs on a request hot path while holding the lock.
            probe = await asyncio.to_thread(self._probe_vram)
            if probe is None:
                # No VRAM probe on this hardware (AMD/Apple/Rockchip or a
                # failed nvidia-smi). Fail OPEN, matching the cluster lease
                # ledger's convention (manager.claim_lease): we cannot prove
                # insufficiency, so admit and keep bookkeeping only. A closed
                # fail here would 503 every model pull on non-NVIDIA hosts.
                free_mb, total_mb = 0, 0
                effective_free = vram_mb  # unknown; grant-log then shows 0
                logger.info(
                    "vram-reservation: no VRAM probe available; admitting %d "
                    "MiB for %r unenforced (bookkeeping only)", vram_mb, caller,
                )
            else:
                free_mb, total_mb = probe
                effective_free = max(0, free_mb - self._reserved_vram_mb)

                if effective_free < vram_mb:
                    logger.debug(
                        "vram-reservation: denied — need %d MiB, have %d MiB "
                        "(%d free − %d reserved)",
                        vram_mb, effective_free, free_mb, self._reserved_vram_mb,
                    )
                    return None

            reservation_id = f"vr_{secrets.token_hex(4)}"
            reservation = VramReservation(
                reservation_id=reservation_id,
                vram_mb=vram_mb,
                caller=caller,
                created_at=time.time(),
            )
            self._reserved_vram_mb += vram_mb
            self._pending[reservation_id] = reservation

            logger.info(
                "vram-reservation: granted %d MiB to %r (id=%s, "
                "total reserved=%d MiB, effective free=%d MiB)",
                vram_mb, caller, reservation_id,
                self._reserved_vram_mb, effective_free - vram_mb,
            )
            return reservation

    def release(self, reservation_id: str) -> bool:
        """Release a reservation.  Idempotent — safe to call repeatedly.

        Returns ``True`` if a reservation was actually released,
        ``False`` if *reservation_id* was unknown or already released.
        """
        reservation = self._pending.pop(reservation_id, None)
        if reservation is not None:
            self._reserved_vram_mb -= reservation.vram_mb
            logger.debug(
                "vram-reservation: released %d MiB for %r (id=%s, "
                "total reserved=%d MiB)",
                reservation.vram_mb, reservation.caller,
                reservation_id, self._reserved_vram_mb,
            )
            return True
        return False

    # ── read-only accessors ─────────────────────────────────────────

    @property
    def reserved_vram_mb(self) -> int:
        """Total VRAM currently reserved (MiB)."""
        return self._reserved_vram_mb

    @property
    def pending_count(self) -> int:
        """Number of active reservations."""
        return len(self._pending)

    def available_vram(self) -> tuple[int, int]:
        """Return ``(effective_free_mb, total_mb)`` after subtracting
        in-flight reservations from the hardware probe.

        When no probe is available, returns ``(0, 0)``; callers on that
        path should already have been admitted fail-open by :meth:`reserve`
        (a deny message never reports these zeros as real capacity).
        """
        probe = self._probe_vram()
        if probe is None:
            return 0, 0
        free_mb, total_mb = probe
        effective_free = max(0, free_mb - self._reserved_vram_mb)
        return effective_free, total_mb

    def stats(self) -> dict:
        """Return a snapshot for monitoring / debug endpoints."""
        probe = self._probe_vram()
        free_mb, total_mb = probe if probe is not None else (0, 0)
        return {
            "probe_available": probe is not None,
            "free_vram_mb": free_mb,
            "total_vram_mb": total_mb,
            "reserved_vram_mb": self._reserved_vram_mb,
            "effective_free_mb": max(0, free_mb - self._reserved_vram_mb),
            "pending_reservations": self.pending_count,
        }

    # ── internal ────────────────────────────────────────────────────

    @staticmethod
    def _probe_vram() -> tuple[int, int] | None:
        """Probe real-time free/total VRAM via nvidia-smi.

        Returns ``(free_mb, total_mb)``, or ``None`` when no nvidia-smi
        is available or the probe fails. ``None`` (cannot know) is
        deliberately distinct from ``(0, 0)`` (known-full): admission
        fails open on ``None``, matching cluster claim_lease semantics.
        """
        try:
            from tinyagentos.system_stats import read_nvidia_vram

            pair = read_nvidia_vram()
            if pair is not None:
                used_mb, total_mb = pair
                free_mb = max(0, total_mb - used_mb)
                return free_mb, total_mb
        except Exception:
            logger.debug("vram-reservation: probe failed", exc_info=True)
        return None
