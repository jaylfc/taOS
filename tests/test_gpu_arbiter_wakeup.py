"""Tests for event-driven admission wakeup — Slice A3 of taOS #1864.

Verifies that releasing a reservation or completing a task immediately
wakes the drain loop (asyncio.Event path) instead of waiting for the
full poll tick.  The 2 s sleep is demoted to fallback timeout only.
"""

import asyncio
import time

import pytest

from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task
from tinyagentos.vram_reservation import VramReservationManager


@pytest.mark.asyncio
async def test_release_triggers_immediate_drain():
    """A reservation release immediately wakes the drain loop.

    The fallback tick is set absurdly long (60 s) so the test can only
    pass through the wake-event path.  When the first task releases its
    VRAM reservation, the second task (queued) should admit in < 0.5 s.
    """
    mgr = VramReservationManager(probe=lambda: (1024, 16384))
    arbiter = GpuArbiter(vram_reservation=mgr, drain_tick_seconds=60.0)
    await arbiter.start()
    try:
        release_first = asyncio.Event()

        async def hold(_res):
            await release_first.wait()
            return "first"

        async def fast(_res):
            return "second"

        t1 = Task(
            capability=Capability.LLM_CHAT, payload=hold,
            preferred_resources=[], priority=Priority.BACKGROUND, submitter="a",
        )
        t2 = Task(
            capability=Capability.LLM_CHAT, payload=fast,
            preferred_resources=[], priority=Priority.BACKGROUND, submitter="b",
        )
        f1 = asyncio.ensure_future(arbiter.submit_gpu(t1, required_vram_mb=1024))
        await asyncio.sleep(0.05)
        f2 = asyncio.ensure_future(arbiter.submit_gpu(t2, required_vram_mb=1024))
        await asyncio.sleep(0.05)               # t2 is queued (VRAM exhausted)
        start = time.monotonic()
        release_first.set()                     # t1 finishes, releases 1024 MiB
        assert await asyncio.wait_for(f2, timeout=1.0) == "second"
        assert time.monotonic() - start < 0.5   # event path, not the 60 s tick
        assert await f1 == "first"
    finally:
        await arbiter.stop()


@pytest.mark.asyncio
async def test_poll_tick_still_drains_without_signal():
    """The fallback poll tick admits work even when no event fires.

    Capacity appears out of band (external process freed VRAM, probe
    changes) — only the periodic tick can discover it.  With a short
    drain_tick_seconds, admission happens within 1 s.
    """
    free = {"mb": 0}
    mgr = VramReservationManager(probe=lambda: (free["mb"], 16384))
    arbiter = GpuArbiter(vram_reservation=mgr, drain_tick_seconds=0.1)
    await arbiter.start()
    try:
        async def fast(_res):
            return "ok"

        t = Task(
            capability=Capability.LLM_CHAT, payload=fast,
            preferred_resources=[], priority=Priority.BACKGROUND, submitter="a",
        )
        f = asyncio.ensure_future(arbiter.submit_gpu(t, required_vram_mb=1024))
        await asyncio.sleep(0.05)
        free["mb"] = 8192                       # external process freed VRAM
        assert await asyncio.wait_for(f, timeout=1.0) == "ok"
    finally:
        await arbiter.stop()
