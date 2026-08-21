"""Tests for GPU arbiter queue ops — op shape, position, cancel, snapshot (taOS #1864 A2)."""

import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task
from tinyagentos.vram_reservation import VramReservationManager


def _mgr(free_mb: int, total_mb: int = 16384) -> VramReservationManager:
    return VramReservationManager(probe=lambda: (free_mb, total_mb))


def _task(priority=Priority.BACKGROUND, submitter="t"):
    async def payload(_res):
        await asyncio.sleep(0.05)
        return "ok"
    return Task(capability=Capability.LLM_CHAT, payload=payload,
                preferred_resources=[], priority=priority, submitter=submitter)


async def _settle(predicate, timeout: float = 2.0, interval: float = 0.05):
    """Poll *predicate* every *interval* seconds until it returns true or *timeout* elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError("settle timeout exceeded")
        await asyncio.sleep(interval)


@pytest.mark.asyncio
async def test_submit_gpu_defaults_backward_compatible():
    arbiter = GpuArbiter(vram_reservation=_mgr(8192))
    result = await arbiter.submit_gpu(_task(), required_vram_mb=1024)
    assert result == "ok"          # old call shape, no new kwargs


@pytest.mark.asyncio
async def test_queue_position_global_for_loads():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))   # everything queues
    t1, t2, t3 = _task(), _task(), _task()
    f1 = asyncio.ensure_future(arbiter.submit_gpu(
        t1, required_vram_mb=1024, op="load", model="a", backend_name="b1"))
    f2 = asyncio.ensure_future(arbiter.submit_gpu(
        t2, required_vram_mb=1024, op="load", model="b", backend_name="b1"))
    f3 = asyncio.ensure_future(arbiter.submit_gpu(
        t3, required_vram_mb=1024, op="load", model="c", backend_name="b1"))
    await _settle(lambda: arbiter.queue_position(t1.id) is not None)
    assert arbiter.queue_position(t1.id) == 1
    assert arbiter.queue_position(t2.id) == 2
    assert arbiter.queue_position(t3.id) == 3
    for f in (f1, f2, f3):
        f.cancel()


@pytest.mark.asyncio
async def test_queue_position_per_model_for_inference():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))
    ta, tb, ta2 = _task(), _task(), _task()
    fs = [asyncio.ensure_future(arbiter.submit_gpu(
              t, required_vram_mb=1024, op="inference", model=m, backend_name="b1"))
          for t, m in ((ta, "m-a"), (tb, "m-b"), (ta2, "m-a"))]
    await _settle(lambda: arbiter.queue_position(ta.id) is not None)
    assert arbiter.queue_position(ta.id) == 1
    assert arbiter.queue_position(tb.id) == 1   # only m-b entries count
    assert arbiter.queue_position(ta2.id) == 2  # behind ta on m-a
    for f in fs:
        f.cancel()


@pytest.mark.asyncio
async def test_queue_snapshot_non_destructive_and_shaped():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))
    t1 = _task(submitter="pull:x")
    f = asyncio.ensure_future(arbiter.submit_gpu(
        t1, required_vram_mb=1024, op="load", model="qwen", backend_name="b1"))
    await _settle(lambda: arbiter.queue_position(t1.id) is not None)
    snap1 = arbiter.queue_snapshot()
    snap2 = arbiter.queue_snapshot()
    entry = snap1[0]
    assert entry["op"] == "load" and entry["model"] == "qwen"
    assert entry["backend_name"] == "b1" and entry["submitter"] == "pull:x"
    assert entry["position"] == 1
    assert [e["task_id"] for e in snap1] == [e["task_id"] for e in snap2]
    stats = await arbiter.stats()
    assert stats["queue_depth"] == 1           # snapshot did not drain
    f.cancel()


@pytest.mark.asyncio
async def test_cancel_queued_op_removes_and_cancels_future():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))
    t1 = _task()
    f = asyncio.ensure_future(arbiter.submit_gpu(
        t1, required_vram_mb=1024, op="load", model="m", backend_name="b1"))
    await _settle(lambda: arbiter.queue_position(t1.id) is not None)
    assert await arbiter.cancel_op(t1.id) is True
    with pytest.raises(asyncio.CancelledError):
        await f
    assert arbiter.queue_position(t1.id) is None
    assert await arbiter.cancel_op(t1.id) is False   # idempotent-ish: gone


@pytest.mark.asyncio
async def test_cancel_running_op_delegates_to_evict():
    mgr = _mgr(8192)
    arbiter = GpuArbiter(vram_reservation=mgr)
    started = asyncio.Event()

    async def payload(_res):
        started.set()
        await asyncio.sleep(30)

    t1 = Task(capability=Capability.LLM_CHAT, payload=payload,
              preferred_resources=[], priority=Priority.BACKGROUND, submitter="t")
    f = asyncio.ensure_future(arbiter.submit_gpu(t1, required_vram_mb=1024))
    await started.wait()
    assert await arbiter.cancel_op(t1.id) is True
    await _settle(lambda: mgr.reserved_vram_mb == 0)
    f.cancel()
    f.cancel()
