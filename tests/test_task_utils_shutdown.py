"""Tests for the bounded background-task shutdown helper (task #64).

The controller restart was slow because the FastAPI lifespan SHUTDOWN phase
gathered background tasks without a timeout: a loop that did not unwind on
cancel blocked the process until systemd SIGKILLed it at TimeoutStopUSec
(~45s). cancel_and_wait caps that wait so shutdown stays a few seconds.
"""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.task_utils import _create_supervised_task, cancel_and_wait


@pytest.mark.asyncio
async def test_cancel_and_wait_cancels_tracked_tasks_promptly():
    """Tracked background loops are cancelled and awaited within the budget."""
    tasks: set = set()

    async def _loop() -> None:
        while True:
            await asyncio.sleep(3600)

    _create_supervised_task(_loop(), tasks)
    _create_supervised_task(_loop(), tasks)
    snapshot = list(tasks)

    stragglers = await asyncio.wait_for(cancel_and_wait(snapshot, timeout=5.0), timeout=2.0)

    assert stragglers == []
    for t in snapshot:
        assert t.done()
        assert t.cancelled()


@pytest.mark.asyncio
async def test_cancel_and_wait_returns_promptly_on_uncancellable_task(caplog):
    """A task that refuses to unwind must not block shutdown past the budget.

    Models a background loop that swallows cancellation and keeps running.
    cancel_and_wait must return within the budget and report the straggler by
    name rather than hang (the bug that stranded restarts for ~45s). Uses
    asyncio.wait, which does not await cancellation of still-pending tasks.
    """
    import logging

    flag = {"stop": False}

    async def _stubborn() -> None:
        while not flag["stop"]:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                # Refuse to die until the test flips the flag.
                continue

    task = asyncio.create_task(_stubborn(), name="stubborn")
    await asyncio.sleep(0)  # let it reach the sleep

    loop = asyncio.get_running_loop()
    start = loop.time()
    with caplog.at_level(logging.WARNING, logger="tinyagentos.task_utils"):
        stragglers = await cancel_and_wait([task], timeout=0.2)
    elapsed = loop.time() - start

    # Returned within the budget (well under the original 45s strand) and named
    # the straggler rather than hanging.
    assert elapsed < 2.0
    assert stragglers == [task]
    assert not task.done()
    assert any("did not exit" in r.message for r in caplog.records)
    assert any("stubborn" in r.message for r in caplog.records)

    # Cleanup: let the loop exit so the test's event loop can close.
    flag["stop"] = True
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass


@pytest.mark.asyncio
async def test_cancel_and_wait_empty_set_is_noop():
    """No tracked tasks → returns immediately with nothing pending."""
    assert await cancel_and_wait(set(), timeout=5.0) == []


@pytest.mark.asyncio
async def test_cancel_and_wait_ignores_already_done_tasks():
    """Already-finished tasks are not re-cancelled and never block."""

    async def _quick() -> None:
        return None

    task = asyncio.create_task(_quick())
    await task
    assert task.done()

    assert await cancel_and_wait([task], timeout=5.0) == []
