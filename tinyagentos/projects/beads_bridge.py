"""Beads bridge: project task graph ↔ A2A coordination channel.

See docs/superpowers/specs/2026-04-27-projects-beads-bridge-design.md.

The bridge has three input edges and one output edge. Inputs:
  - mark_dirty(project_id) from route hooks after task/relationship mutations
  - on_chat_message(...) from chat send hooks
  - on_event(project_id, evt) from a broker subscription

Output: a single async writer task drains the dirty set on debounce and
writes data/projects/<slug>/.beads/tasks.jsonl. Per-project asyncio.Lock
serializes renders for the same project so concurrent ticks can't race.

Failure isolation rule: every public entry point catches and logs;
nothing the bridge does can break a route or boot. Mirror of
tinyagentos/projects/a2a.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Shutdown drain budget — how long stop() waits for the final tick.
_STOP_DRAIN_TIMEOUT = 2.0


class BeadsBridge:
    def __init__(
        self,
        *,
        project_store,
        task_store,
        channel_store,
        msg_store,
        broker,
        data_root: Path,
        debounce_seconds: float = 0.2,
    ) -> None:
        self._project_store = project_store
        self._task_store = task_store
        self._channel_store = channel_store
        self._msg_store = msg_store
        self._broker = broker
        self._data_root = Path(data_root)
        self._debounce = float(debounce_seconds)

        self._dirty: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._writer_task: asyncio.Task | None = None
        self._broker_tasks: dict[str, asyncio.Task] = {}
        self._broker_queues: dict[str, Any] = {}  # project_id -> Queue
        self._stopped = asyncio.Event()
        # Bounded dedupe of (message_id, task_id) pairs already attached
        # as comments. FIFO eviction once cap is hit; collisions after
        # eviction merely create one duplicate comment row.
        self._seen_comments_set: set[tuple[str, str]] = set()
        self._seen_comments_order: deque[tuple[str, str]] = deque(maxlen=1024)

    async def start(self) -> None:
        if self._writer_task is not None:
            return
        self._stopped.clear()
        self._writer_task = asyncio.create_task(
            self._writer_loop(), name="beads-bridge-writer"
        )

    async def stop(self) -> None:
        if self._writer_task is None:
            return
        self._stopped.set()
        try:
            await asyncio.wait_for(self._writer_task, timeout=_STOP_DRAIN_TIMEOUT)
        except asyncio.TimeoutError:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            self._writer_task = None
        # Cancel any per-project broker subscriber tasks
        for t in self._broker_tasks.values():
            t.cancel()
        for t in self._broker_tasks.values():
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._broker_tasks.clear()
        self._broker_queues.clear()

    def mark_dirty(self, project_id: str) -> None:
        if not project_id:
            return
        self._dirty.add(project_id)

    async def _writer_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.sleep(self._debounce)
                if not self._dirty:
                    continue
                # Snapshot and clear; a fresh mutation during render will
                # re-add the project.
                pending = list(self._dirty)
                self._dirty.clear()
                for project_id in pending:
                    try:
                        async with self._locks[project_id]:
                            await self._render_jsonl(project_id)
                    except Exception:
                        logger.exception(
                            "beads bridge: render failed for %s", project_id
                        )
                        # Re-mark so the next tick retries.
                        self._dirty.add(project_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("beads bridge: writer loop iteration crashed")

    async def _render_jsonl(self, project_id: str) -> None:
        """Render the project's tasks.jsonl. Implemented in Task 6."""
        raise NotImplementedError
