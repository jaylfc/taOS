"""Asyncio background-task utilities.

Provides _create_supervised_task, a thin wrapper around asyncio.create_task
that tracks the task in a caller-supplied set and logs any unhandled
exceptions via a done callback.
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
