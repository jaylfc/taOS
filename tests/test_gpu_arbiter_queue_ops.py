"""Tests for GPU arbiter queue ops — op shape, position, cancel, snapshot (taOS #1864 A2)."""

import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter, _QueuedGpuTask, GpuAdmission
from tinyagentos.scheduler.types import Capability, NoResourceAvailableError, Priority, Task
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
    await _settle(lambda: all(arbiter.queue_position(t.id) is not None for t in (t1, t2, t3)))
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
    await _settle(lambda: all(arbiter.queue_position(t.id) is not None for t in (ta, tb, ta2)))
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


@pytest.mark.asyncio
async def test_queue_snapshot_exposes_resource_id():
    """queue_snapshot() must surface resource_id so fence handlers can discover
    which queued ops target a fenced node without touching private state."""
    arbiter = GpuArbiter(vram_reservation=_mgr(0))   # everything queues
    t1 = _task(submitter="pull:x")
    f = asyncio.ensure_future(arbiter.submit_gpu(
        t1, required_vram_mb=1024, op="load", model="qwen",
        backend_name="b1", resource_id="gpu-node-1:gpu-0"))
    await asyncio.sleep(0.05)      # let it enqueue
    snap = arbiter.queue_snapshot()
    assert len(snap) == 1
    assert snap[0]["resource_id"] == "gpu-node-1:gpu-0"
    assert "resource_id" in snap[0]
    f.cancel()


@pytest.mark.asyncio
async def test_cancel_queued_for_resource_cancels_matching():
    """cancel_queued_for_resource cancels every queued op for a resource_id,
    surfacing resource_id in the snapshot and removing entries from the
    queue. Bulk + selective: only the targeted resource is cancelled."""
    arbiter = GpuArbiter(vram_reservation=_mgr(0))   # everything queues
    t_a1 = _task(submitter="a1")
    t_a2 = _task(submitter="a2")
    t_b = _task(submitter="b")
    f_a1 = asyncio.ensure_future(arbiter.submit_gpu(
        t_a1, required_vram_mb=1024, op="load", model="m1",
        backend_name="b1", resource_id="gpu-node-1:gpu-0"))
    f_a2 = asyncio.ensure_future(arbiter.submit_gpu(
        t_a2, required_vram_mb=1024, op="load", model="m2",
        backend_name="b1", resource_id="gpu-node-1:gpu-0"))
    f_b = asyncio.ensure_future(arbiter.submit_gpu(
        t_b, required_vram_mb=1024, op="load", model="m3",
        backend_name="b1", resource_id="gpu-node-2:gpu-0"))
    await asyncio.sleep(0.05)      # let all three enqueue
    assert len(arbiter.queue_snapshot()) == 3

    cancelled = await arbiter.cancel_queued_for_resource("gpu-node-1:gpu-0")
    assert cancelled == 2                          # bulk: both gpu-node-1 ops

    # Targeted ops must have their futures cancelled (arbiter cancel semantics
    # identical to cancel_op).
    with pytest.raises(asyncio.CancelledError):
        await f_a1
    with pytest.raises(asyncio.CancelledError):
        await f_a2

    # Entries removed from the queue; the non-matching op survives.
    assert t_a1.id not in arbiter._queued_entries
    assert t_a2.id not in arbiter._queued_entries
    assert t_b.id in arbiter._queued_entries
    snap = arbiter.queue_snapshot()
    assert [e["task_id"] for e in snap] == [t_b.id]
    assert snap[0]["resource_id"] == "gpu-node-2:gpu-0"

    f_b.cancel()


@pytest.mark.asyncio
async def test_queue_full_drop_removes_queued_entry():
    """When _drain_queue cannot re-queue a retry entry because the queue is
    full, the dropped task_id must be removed from _queued_entries so it
    does not appear as a phantom in queue_snapshot() and queue_position()."""
    arbiter = GpuArbiter(
        vram_probe=lambda: (0, 16384),
        max_queue_size=1,
    )

    t1 = Task(id="t1", capability=Capability.LLM_CHAT,
              payload=lambda _res: asyncio.sleep(0.05), preferred_resources=[],
              priority=Priority.BACKGROUND, submitter="t")
    t2 = Task(id="t2", capability=Capability.LLM_CHAT,
              payload=lambda _res: asyncio.sleep(0.05), preferred_resources=[],
              priority=Priority.BACKGROUND, submitter="t")

    loop = asyncio.get_running_loop()
    f1 = loop.create_future()
    f2 = loop.create_future()
    t1._arbiter_future = f1  # type: ignore[attr-defined]
    t2._arbiter_future = f2  # type: ignore[attr-defined]

    entry1 = _QueuedGpuTask(
        priority=int(t1.priority), seq=1, task=t1,
        required_vram_mb=1024, evictable=False,
    )
    await arbiter._queue.put(entry1)
    arbiter._queued_entries[t1.id] = entry1

    original = arbiter._reserve_and_check

    async def yielding_reserve(task_id, required_vram_mb):
        await asyncio.sleep(0)
        return GpuAdmission(
            admitted=False, free_vram_mb=0, required_vram_mb=required_vram_mb,
            reason="test: no VRAM",
        )

    arbiter._reserve_and_check = yielding_reserve  # type: ignore[method-assign]

    async def add_t2():
        entry2 = _QueuedGpuTask(
            priority=int(t2.priority), seq=2, task=t2,
            required_vram_mb=1024, evictable=False,
        )
        await arbiter._queue.put(entry2)
        arbiter._queued_entries[t2.id] = entry2

    add_task = asyncio.ensure_future(add_t2())

    try:
        await arbiter._drain_queue()
    finally:
        arbiter._reserve_and_check = original  # type: ignore[method-assign]

    # t2 was the second retry entry, dropped because queue was full (held t1)
    assert t2.id not in arbiter._queued_entries
    assert arbiter.queue_position(t2.id) is None
    assert t2.id not in [e["task_id"] for e in arbiter.queue_snapshot()]
    assert f2.done()
    assert isinstance(f2.exception(), NoResourceAvailableError)

    # t1 was the first retry entry, successfully re-queued
    assert t1.id in arbiter._queued_entries

    f1.cancel()
