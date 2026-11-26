"""Regression: ClusterManager._monitor_task_done must survive cancellation.

The callback is registered on the monitor task, which app shutdown
cancels on purpose. For a cancelled task, ``task.exception()`` raises
``CancelledError`` instead of returning it -- so the callback itself
crashed with "Exception in callback" at every shutdown, which pytest
surfaces as a setup/teardown error in whichever app-boot test file
runs (it blocked PR #4567 via tests/test_version_endpoint.py). The
callback also restarted the monitor loop unconditionally, including
during shutdown.
"""
import asyncio

import pytest

from tinyagentos.cluster.manager import ClusterManager


class _Stub:
    """Just the attributes _monitor_task_done touches."""

    def __init__(self):
        self._monitor_task = None
        self.restarts = 0

    async def _monitor_loop(self):
        self.restarts += 1
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_done_callback_ignores_cancelled_task():
    stub = _Stub()

    async def _sleep():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_sleep())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Must not raise, and must not restart the monitor during shutdown.
    ClusterManager._monitor_task_done(stub, task)
    assert stub.restarts == 0
    assert stub._monitor_task is None


@pytest.mark.asyncio
async def test_done_callback_restarts_after_crash():
    stub = _Stub()

    async def _crash():
        raise RuntimeError("monitor boom")

    task = asyncio.create_task(_crash())
    await asyncio.sleep(0.05)
    assert task.done() and not task.cancelled()

    ClusterManager._monitor_task_done(stub, task)
    assert stub._monitor_task is not None
    await asyncio.sleep(0.05)  # let the restarted loop actually start
    stub._monitor_task.cancel()
    await asyncio.sleep(0.05)
    assert stub.restarts == 1
