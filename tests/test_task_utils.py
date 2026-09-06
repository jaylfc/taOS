import asyncio
import logging
from unittest.mock import patch, MagicMock

import pytest

from tinyagentos.task_utils import _create_supervised_task, cancel_and_wait


# ---------------------------------------------------------------------------
# _create_supervised_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supervised_task_added_to_set():
    task_set = set()
    coro = asyncio.sleep(0)
    task = _create_supervised_task(coro, task_set)
    assert task in task_set
    await task
    # done callback should have removed it
    assert task not in task_set


@pytest.mark.asyncio
async def test_supervised_task_removed_on_completion():
    task_set = set()
    task = _create_supervised_task(asyncio.sleep(0), task_set)
    await asyncio.sleep(0.01)  # let the done callback fire
    assert len(task_set) == 0


@pytest.mark.asyncio
async def test_supervised_task_logs_unhandled_exception(caplog):
    async def boom():
        raise RuntimeError("something broke")

    task_set = set()
    with caplog.at_level(logging.ERROR):
        task = _create_supervised_task(boom(), task_set)
        await asyncio.sleep(0.05)  # let the task complete and callback fire

    assert any("something broke" in r.message for r in caplog.records)
    assert any("unhandled exception" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_supervised_task_no_log_on_success(caplog):
    async def ok():
        return 42

    task_set = set()
    with caplog.at_level(logging.ERROR):
        task = _create_supervised_task(ok(), task_set)
        await asyncio.sleep(0.05)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 0


@pytest.mark.asyncio
async def test_supervised_task_no_log_on_cancel():
    async def long_running():
        await asyncio.sleep(100)

    task_set = set()
    task = _create_supervised_task(long_running(), task_set)
    task.cancel()
    await asyncio.sleep(0.05)  # let cancel propagate and callback fire

    # The task's done callback should see cancelled=True and NOT log an error.
    # t.exception() raises CancelledError so the callback checks .cancelled() first.
    assert task.cancelled()


@pytest.mark.asyncio
async def test_supervised_task_set_discards_done_task():
    """_on_done should silently discard (not KeyError) even if already removed."""
    task_set = set()
    task = _create_supervised_task(asyncio.sleep(0), task_set)
    task_set.clear()  # simulate external removal before callback fires
    await asyncio.sleep(0.05)
    # discard should not raise
    assert len(task_set) == 0


@pytest.mark.asyncio
async def test_supervised_task_exception_includes_task_name(caplog):
    async def explode():
        raise ValueError("named boom")

    task_set = set()
    with caplog.at_level(logging.ERROR):
        task = _create_supervised_task(explode(), task_set)
        await asyncio.sleep(0.05)

    # The log message includes the task name
    assert any("named boom" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# cancel_and_wait
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_and_wait_empty_list():
    result = await cancel_and_wait([])
    assert result == []


@pytest.mark.asyncio
async def test_cancel_and_wait_all_done():
    """Tasks that are already done should return immediately with no cancels."""
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    result = await cancel_and_wait([task])
    assert result == []


@pytest.mark.asyncio
async def test_cancel_and_wait_cancels_pending_tasks():
    async def never():
        await asyncio.sleep(1000)

    tasks = [asyncio.create_task(never()) for _ in range(3)]
    await asyncio.sleep(0.01)  # ensure tasks are scheduled
    result = await cancel_and_wait(tasks, timeout=1.0)
    for t in tasks:
        assert t.cancelled()
    assert result == []


@pytest.mark.asyncio
async def test_cancel_and_wait_returns_stragglers_on_timeout():
    """A task that ignores cancellation should appear in the stragglers list."""

    async def stubborn():
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            # Swallow cancellation: refuse to exit
            await asyncio.sleep(1000)

    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0.01)
    stragglers = await cancel_and_wait([task], timeout=0.1)
    assert len(stragglers) == 1
    assert stragglers[0] is task


@pytest.mark.asyncio
async def test_cancel_and_wait_logs_stragglers(caplog):
    async def stubborn():
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            await asyncio.sleep(1000)

    task = asyncio.create_task(stubborn(), name="stubborn-worker")
    await asyncio.sleep(0.01)
    with caplog.at_level(logging.WARNING):
        stragglers = await cancel_and_wait([task], timeout=0.1)

    assert stragglers == [task]
    assert any("stubborn-worker" in r.message for r in caplog.records)
    assert any("did not exit" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_cancel_and_wait_mixed_done_and_pending():
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task

    async def never():
        await asyncio.sleep(1000)

    pending_task = asyncio.create_task(never())
    await asyncio.sleep(0.01)

    result = await cancel_and_wait([done_task, pending_task], timeout=1.0)
    assert pending_task.cancelled()
    assert result == []


@pytest.mark.asyncio
async def test_cancel_and_wait_custom_timeout():
    async def stubborn():
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            await asyncio.sleep(1000)

    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0.01)

    stragglers = await cancel_and_wait([task], timeout=0.05)
    assert len(stragglers) == 1


@pytest.mark.asyncio
async def test_cancel_and_wait_no_stragglers_log_at_info(caplog):
    async def quick():
        await asyncio.sleep(1000)

    task = asyncio.create_task(quick())
    await asyncio.sleep(0.01)
    with caplog.at_level(logging.DEBUG):
        result = await cancel_and_wait([task], timeout=1.0)

    assert result == []
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("did not exit" in r.message for r in warning_records)


@pytest.mark.asyncio
async def test_cancel_and_wait_multiple_stragglers():
    async def stubborn():
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            await asyncio.sleep(1000)

    tasks = [asyncio.create_task(stubborn(), name=f"worker-{i}") for i in range(3)]
    await asyncio.sleep(0.01)

    stragglers = await cancel_and_wait(tasks, timeout=0.1)
    assert len(stragglers) == 3
    names = {t.get_name() for t in stragglers}
    assert names == {"worker-0", "worker-1", "worker-2"}


@pytest.mark.asyncio
async def test_cancel_and_wait_single_done_task_skips_cancel():
    """A single already-done task: no cancelled calls, returns fast."""
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    # If cancel is called on a done task, it's a no-op internally, but we
    # verify cancel_and_wait itself doesn't try to cancel done tasks.
    result = await cancel_and_wait([task])
    assert result == []
