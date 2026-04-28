"""Debounced .tldr snapshotter for project canvases.

Mirrors tinyagentos/projects/beads_bridge.py: subscribe to broker,
mark dirty on canvas events, drain periodically. DB is authoritative;
the .tldr file is a derived snapshot — we never read it back.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STOP_DRAIN_TIMEOUT = 2.0
_TLDRAW_SCHEMA_VERSION = 1


class CanvasSnapshotter:
    def __init__(
        self,
        *,
        project_store,
        canvas_store,
        broker,
        data_root: Path,
        debounce_seconds: float = 0.5,
    ) -> None:
        self._project_store = project_store
        self._canvas_store = canvas_store
        self._broker = broker
        self._data_root = Path(data_root)
        self._debounce = float(debounce_seconds)

        self._dirty: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._writer_task: asyncio.Task | None = None
        self._broker_tasks: dict[str, asyncio.Task] = {}
        self._broker_queues: dict[str, Any] = {}
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._writer_task is not None:
            return
        self._stopped.clear()
        self._writer_task = asyncio.create_task(
            self._writer_loop(), name="canvas-snapshotter"
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
        if project_id:
            self._dirty.add(project_id)

    async def backfill_active(self) -> int:
        try:
            projects = await self._project_store.list_projects(status="active")
        except Exception:
            logger.exception("canvas snapshotter: list_projects failed")
            return 0
        n = 0
        for p in projects:
            self.mark_dirty(p["id"])
            await self._ensure_subscribed(p["id"])
            n += 1
        return n

    async def _ensure_subscribed(self, project_id: str) -> None:
        if project_id in self._broker_tasks:
            return
        try:
            queue = await self._broker.subscribe(project_id)
        except Exception:
            logger.exception("canvas snapshotter: subscribe failed for %s", project_id)
            return
        self._broker_queues[project_id] = queue
        self._broker_tasks[project_id] = asyncio.create_task(
            self._broker_loop(project_id, queue),
            name=f"canvas-snapshotter-broker:{project_id}",
        )

    async def _broker_loop(self, project_id: str, queue: Any) -> None:
        try:
            while not self._stopped.is_set():
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if str(ev.kind).startswith("canvas."):
                    self.mark_dirty(project_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "canvas snapshotter: broker loop crashed for %s", project_id
            )
        finally:
            try:
                await self._broker.unsubscribe(project_id, queue)
            except Exception:
                pass

    async def export_now(self, project_id: str) -> Path | None:
        await self._ensure_subscribed(project_id)
        async with self._locks[project_id]:
            return await self._render_tldr(project_id)

    async def _writer_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.sleep(self._debounce)
                if not self._dirty:
                    continue
                pending = list(self._dirty)
                self._dirty.clear()
                for project_id in pending:
                    try:
                        async with self._locks[project_id]:
                            await self._render_tldr(project_id)
                    except Exception:
                        logger.exception(
                            "canvas snapshotter: render failed for %s", project_id
                        )
                        self._dirty.add(project_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("canvas snapshotter: writer iteration crashed")

    async def _render_tldr(self, project_id: str) -> Path | None:
        project = await self._project_store.get_project(project_id)
        if project is None:
            return None
        slug = project["slug"]
        canvas_dir = self._data_root / slug / "canvas"
        canvas_dir.mkdir(parents=True, exist_ok=True)
        target = canvas_dir / "board.tldr"
        tmp = canvas_dir / f"board.tldr.{os.getpid()}.tmp"

        elements = await self._canvas_store.list_elements(project_id)
        snapshot = _build_tldraw_snapshot(elements)
        tmp.write_text(json.dumps(snapshot, separators=(",", ":")))
        os.replace(tmp, target)
        return target


def _build_tldraw_snapshot(elements: list[dict]) -> dict:
    store: dict[str, dict] = {
        "document:document": {
            "id": "document:document",
            "typeName": "document",
            "name": "",
        },
        "page:page": {
            "id": "page:page",
            "typeName": "page",
            "name": "Page 1",
            "index": "a1",
        },
    }
    for el in elements:
        store[f"shape:{el['id']}"] = {
            "id": f"shape:{el['id']}",
            "typeName": "shape",
            "type": _tldraw_shape_type(el["kind"]),
            "x": el["x"],
            "y": el["y"],
            "rotation": el["rotation"],
            "index": "a1",
            "parentId": "page:page",
            "isLocked": False,
            "opacity": 1,
            "props": {
                "w": el["w"],
                "h": el["h"],
                "taos_kind": el["kind"],
                "taos_payload": el["payload"],
                "taos_author_id": el["author_id"],
                "taos_author_kind": el["author_kind"],
            },
            "meta": {},
        }
    return {
        "schema": {"schemaVersion": 2, "storeVersion": _TLDRAW_SCHEMA_VERSION},
        "store": store,
    }


def _tldraw_shape_type(kind: str) -> str:
    if kind == "note":
        return "taos-note"
    if kind == "link":
        return "taos-link"
    if kind == "image":
        return "taos-image"
    return "geo"
