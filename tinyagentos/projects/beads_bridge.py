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

    async def backfill_active(self) -> int:
        """Mark every active project dirty and subscribe to broker. Called at boot."""
        try:
            projects = await self._project_store.list_projects(status="active")
        except Exception:
            logger.exception("beads bridge: backfill list_projects failed")
            return 0
        count = 0
        for p in projects:
            self.mark_dirty(p["id"])
            await self._ensure_subscribed(p["id"])
            count += 1
        return count

    async def _ensure_subscribed(self, project_id: str) -> None:
        if project_id in self._broker_tasks:
            return
        try:
            queue = await self._broker.subscribe(project_id)
        except Exception:
            logger.exception(
                "beads bridge: broker subscribe failed for %s", project_id
            )
            return
        self._broker_queues[project_id] = queue
        self._broker_tasks[project_id] = asyncio.create_task(
            self._broker_loop(project_id, queue),
            name=f"beads-bridge-broker:{project_id}",
        )

    async def _broker_loop(self, project_id: str, queue: Any) -> None:
        try:
            while not self._stopped.is_set():
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                # ProjectEvent dataclass has .kind and .payload
                event = {"kind": ev.kind, "payload": ev.payload}
                await self.on_event(project_id, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "beads bridge: broker loop crashed for %s", project_id
            )
        finally:
            try:
                await self._broker.unsubscribe(project_id, queue)
            except Exception:
                pass

    async def export_now(self, project_id: str) -> Path | None:
        """Synchronous render-and-write. Returns the file path, or None
        if the project doesn't exist."""
        project = await self._project_store.get_project(project_id)
        if project is None:
            return None
        async with self._locks[project_id]:
            await self._render_jsonl(project_id)
        return self._data_root / project["slug"] / ".beads" / "tasks.jsonl"

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
        project = await self._project_store.get_project(project_id)
        if project is None:
            return
        slug = project["slug"]
        beads_dir = self._data_root / slug / ".beads"
        beads_dir.mkdir(parents=True, exist_ok=True)
        target = beads_dir / "tasks.jsonl"
        tmp = beads_dir / f"tasks.jsonl.{os.getpid()}.tmp"

        tasks = await self._task_store.list_tasks(project_id=project_id)
        from tinyagentos.projects.beads_format import (
            compute_ready,
            task_to_jsonl_dict,
        )
        import json

        lines: list[str] = []
        for t in tasks:
            outbound = await self._task_store.list_relationships(
                t["id"], direction="from"
            )
            incoming = await self._task_store.list_relationships(
                t["id"], direction="to"
            )
            incoming_blocker_statuses: list[str] = []
            for rel in incoming:
                if rel.get("kind") != "blocks":
                    continue
                src = await self._task_store.get_task(rel["from_task_id"])
                if src is not None:
                    incoming_blocker_statuses.append(src.get("status", "open"))
            ready = compute_ready(t, incoming_blocker_statuses)
            lines.append(
                json.dumps(task_to_jsonl_dict(t, outbound, ready), separators=(",", ":"))
            )

        tmp.write_text("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, target)

    async def _find_a2a_channel(self, project_id: str) -> dict | None:
        """Resolve the project's A2A channel. None if missing/archived."""
        try:
            channels = await self._channel_store.list_channels(
                project_id=project_id, archived=False,
            )
        except Exception:
            logger.exception("beads bridge: list_channels failed for %s", project_id)
            return None
        for ch in channels:
            if (
                ch.get("name") == "a2a"
                and ch.get("type") == "group"
                and (ch.get("settings") or {}).get("kind") == "a2a"
            ):
                return ch
        return None

    async def _post_system(self, channel_id: str, body: str) -> None:
        try:
            await self._msg_store.send_message(
                channel_id=channel_id,
                author_id="bridge",
                author_type="system",
                content=body,
                content_type="system",
                state="complete",
            )
        except Exception:
            logger.exception("beads bridge: send_message failed for %s", channel_id)

    async def on_event(self, project_id: str, event: dict) -> None:
        try:
            kind = event.get("kind")
            if kind not in ("task.claimed", "task.released", "task.closed"):
                return
            payload = event.get("payload") or {}
            tsk_id = payload.get("id")
            if not tsk_id:
                return
            channel = await self._find_a2a_channel(project_id)
            if channel is None:
                return
            task = await self._task_store.get_task(tsk_id)
            if task is None:
                return
            title = task.get("title", "")
            from tinyagentos.projects.beads_format import (
                format_claimed,
                format_closed,
                format_released,
            )
            if kind == "task.claimed":
                actor = payload.get("claimed_by") or task.get("claimed_by") or "agent"
                await self._post_system(
                    channel["id"], format_claimed(actor, tsk_id, title)
                )
            elif kind == "task.released":
                # The release event payload doesn't include releaser_id;
                # use the actor we last knew about, or fall back to "agent".
                actor = task.get("claimed_by") or "agent"
                await self._post_system(
                    channel["id"], format_released(actor, tsk_id, title)
                )
            elif kind == "task.closed":
                actor = payload.get("closed_by") or task.get("closed_by") or "agent"
                note = task.get("close_reason")
                await self._post_system(
                    channel["id"], format_closed(actor, tsk_id, title, note)
                )
                await self._announce_newly_ready(channel["id"], project_id, tsk_id)
        except Exception:
            logger.exception(
                "beads bridge: on_event crashed for %s/%s", project_id, event
            )

    async def _announce_newly_ready(
        self, channel_id: str, project_id: str, closed_task_id: str
    ) -> None:
        from tinyagentos.projects.beads_format import format_ready
        try:
            outbound = await self._task_store.list_relationships(
                closed_task_id, direction="from"
            )
        except Exception:
            logger.exception(
                "beads bridge: list_relationships failed for %s", closed_task_id
            )
            return
        for rel in outbound:
            if rel.get("kind") != "blocks":
                continue
            dependent_id = rel["to_task_id"]
            dep = await self._task_store.get_task(dependent_id)
            if dep is None or dep.get("status") != "open":
                continue
            # Check every other blocker on this dependent is also closed.
            other_blockers = await self._task_store.list_relationships(
                dependent_id, direction="to"
            )
            still_blocked = False
            for other in other_blockers:
                if other.get("kind") != "blocks":
                    continue
                if other["from_task_id"] == closed_task_id:
                    continue
                src = await self._task_store.get_task(other["from_task_id"])
                if src is not None and src.get("status") not in ("closed", "cancelled"):
                    still_blocked = True
                    break
            if still_blocked:
                continue
            await self._post_system(
                channel_id,
                format_ready(dependent_id, dep.get("title", ""), list(dep.get("labels") or [])),
            )

    async def on_chat_message(
        self, project_id: str, channel_id: str, message: dict
    ) -> None:
        """Chat send hook. Filters non-A2A channels and our own system
        messages, then dispatches verbs and attaches mention comments."""
        try:
            if message.get("content_type") == "system":
                return
            channel = await self._find_a2a_channel(project_id)
            if channel is None or channel["id"] != channel_id:
                return
            body = message.get("content") or ""
            author = message.get("author_id") or "agent"
            verb_ids = await self._dispatch_verbs(body, author)
            await self._attach_mentions(
                project_id=project_id,
                message_id=message.get("id") or "",
                author=author,
                body=body,
                verb_ids=verb_ids,
            )
        except Exception:
            logger.exception(
                "beads bridge: on_chat_message crashed for %s/%s",
                project_id, channel_id,
            )

    async def _dispatch_verbs(self, body: str, author: str) -> set[str]:
        from tinyagentos.projects.beads_format import parse_verbs
        acted: set[str] = set()
        for verb, tsk_id, note in parse_verbs(body):
            acted.add(tsk_id)
            try:
                if verb == "claim":
                    await self._task_store.claim_task(tsk_id, author)
                elif verb == "release":
                    await self._task_store.release_task(tsk_id, author)
                elif verb == "close":
                    await self._task_store.close_task(
                        tsk_id, closed_by=author, reason=note
                    )
            except Exception:
                logger.info(
                    "beads bridge: verb /%s %s by %s failed",
                    verb, tsk_id, author, exc_info=True,
                )
        return acted

    async def _attach_mentions(
        self,
        *,
        project_id: str,
        message_id: str,
        author: str,
        body: str,
        verb_ids: set[str],
    ) -> None:
        from tinyagentos.projects.beads_format import scan_task_ids
        for tsk_id in scan_task_ids(body):
            if tsk_id in verb_ids:
                continue
            key = (message_id, tsk_id)
            if key in self._seen_comments_set:
                continue
            try:
                task = await self._task_store.get_task(tsk_id)
            except Exception:
                logger.exception(
                    "beads bridge: get_task failed for %s", tsk_id,
                )
                continue
            if task is None or task.get("project_id") != project_id:
                continue
            try:
                await self._task_store.add_comment(
                    task_id=tsk_id, author_id=author, body=body,
                )
            except Exception:
                logger.info(
                    "beads bridge: add_comment failed for %s", tsk_id,
                    exc_info=True,
                )
                continue
            # Record only after successful attach. Bounded FIFO eviction.
            if len(self._seen_comments_order) == self._seen_comments_order.maxlen:
                evicted = self._seen_comments_order[0]
                self._seen_comments_set.discard(evicted)
            self._seen_comments_order.append(key)
            self._seen_comments_set.add(key)
