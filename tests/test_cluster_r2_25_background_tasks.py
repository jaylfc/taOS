"""RED tests for R2-25: fire-and-forget tasks must survive GC.

The manager must keep strong references to background asyncio tasks so that
``gc.collect()`` before the event loop gets a chance to run them does not
collect (cancel) the task mid-flight.  ``stop()`` must also drain outstanding
tasks and log any exceptions surfaced by the done-callback.
"""
from __future__ import annotations

import asyncio
import gc
import logging

import pytest

from tinyagentos.cluster.manager import ClusterManager


@pytest.mark.asyncio
class TestSpawnBackgroundTaskSurvivesGC:
    """R2-25: fire-and-forget tasks must not be garbage-collected mid-flight."""

    async def test_task_survives_gc_before_execution(self):
        """Creating a task via the manager helper and gc.collect()-ing
        before the loop runs must NOT cancel the task, it should still
        complete."""
        mgr = ClusterManager()

        started = asyncio.Event()

        async def _work():
            started.set()

        task = mgr._spawn_background_task(_work())
        gc.collect()

        # Wait for the task to actually run.  If GC cancelled it, this
        # raises TimeoutError.
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.wait_for(asyncio.shield(task), timeout=5)
        assert task.done()
        await mgr.stop()

    async def test_task_result_preserved(self):
        """The helper must return the task so callers can inspect results."""
        mgr = ClusterManager()

        async def _work():
            await asyncio.sleep(0.01)
            return "ok"

        task = mgr._spawn_background_task(_work())
        result = await task
        assert result == "ok"
        await mgr.stop()

    async def test_stop_awaits_outstanding_tasks(self):
        """stop() must drain _background_tasks so fire-and-forget tasks
        complete before shutdown."""
        mgr = ClusterManager()
        mgr._monitor_task = None

        flag = {"done": False}

        async def _work():
            await asyncio.sleep(0.05)
            flag["done"] = True

        mgr._spawn_background_task(_work())
        await mgr.stop()

        assert flag["done"] is True
        assert len(mgr._background_tasks) == 0

    async def test_exception_logged_in_callback(self, caplog):
        """Exceptions inside background tasks must be logged by the done
        callback, not silently swallowed."""
        mgr = ClusterManager()

        async def _boom():
            raise ValueError("kaboom")

        with caplog.at_level(logging.ERROR, logger="tinyagentos.cluster.manager"):
            mgr._spawn_background_task(_boom())
            # Let the event loop run so the task and its callback fire.
            await asyncio.sleep(0.1)

        assert any(
            record.levelno >= logging.ERROR and "Background task" in record.getMessage()
            for record in caplog.records
        ), f"Expected logged error for failed background task, got: {[r.getMessage() for r in caplog.records]}"
        await mgr.stop()
