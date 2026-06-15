"""Asyncio background-task utilities.

Provides _create_supervised_task, a thin wrapper around asyncio.create_task
that tracks the task in a caller-supplied set and logs any unhandled
exceptions via a done callback, plus cancel_and_wait, a bounded shutdown
helper that cancels tracked tasks without blocking the process forever.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def _create_supervised_task(
    coro,
    task_set: set,
) -> asyncio.Task:
    """Create an asyncio task, store it in *task_set*, and log any exception.

    The done callback removes the task from *task_set* so the set never holds
    references to completed tasks (avoids memory growth) and cancelled tasks
    during shutdown are automatically removed.
    """
    task = asyncio.create_task(coro)

    def _on_done(t: asyncio.Task) -> None:
        task_set.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "background task %r raised an unhandled exception: %s",
                    t.get_name(),
                    exc,
                    exc_info=exc,
                )

    task.add_done_callback(_on_done)
    task_set.add(task)
    return task


async def cancel_and_wait(
    tasks,
    *,
    timeout: float = 5.0,
) -> list[asyncio.Task]:
    """Cancel *tasks* and wait for them to exit under a bounded *timeout*.

    Used at lifespan shutdown so a misbehaving background loop cannot block the
    process indefinitely (systemd then SIGKILLs after TimeoutStopUSec, which is
    what made controller restarts take ~45s). Cancellation is requested on every
    not-yet-done task, then we wait with asyncio.wait(timeout=...).

    asyncio.wait (unlike asyncio.wait_for + gather) does NOT await the
    cancellation of tasks that are still pending when the deadline passes, so a
    task that refuses to unwind cannot make this call hang. Any straggler is
    logged by name and returned; we proceed rather than block forever.

    Returns the list of tasks that were still running when the timeout elapsed
    (empty when everything exited cleanly).
    """
    pending = [t for t in tasks if not t.done()]
    for t in pending:
        t.cancel()
    if not pending:
        return []
    _, still_pending = await asyncio.wait(pending, timeout=timeout)
    stragglers = list(still_pending)
    if stragglers:
        logger.warning(
            "shutdown: %d background task(s) did not exit within %.1fs: %s",
            len(stragglers),
            timeout,
            ", ".join(t.get_name() for t in stragglers) or "<unnamed>",
        )
    return stragglers
