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

# .tldr export format. Kept as plain data so this module stays pure Python and
# survives removing the tldraw npm package (see #2132) -- the export is exactly
# the escape hatch users need AFTER we stop shipping tldraw, so it must not
# depend on it.
#
# Captured from tldraw 4.5.12 via createTLSchema().serialize(). tldraw migrates
# older schemas forward on open, so this staying fixed is fine; refresh it only
# if an export stops opening in a current tldraw.
_TLDRAW_FILE_FORMAT_VERSION = 1
_TLDRAW_SERIALIZED_SCHEMA = {
    "schemaVersion": 2,
    "sequences": {
        "com.tldraw.store": 5,
        "com.tldraw.asset": 1,
        "com.tldraw.camera": 1,
        "com.tldraw.document": 2,
        "com.tldraw.instance": 26,
        "com.tldraw.instance_page_state": 5,
        "com.tldraw.page": 1,
        "com.tldraw.instance_presence": 6,
        "com.tldraw.pointer": 1,
        "com.tldraw.shape": 4,
        "com.tldraw.asset.bookmark": 2,
        "com.tldraw.asset.image": 6,
        "com.tldraw.asset.video": 5,
        "com.tldraw.shape.arrow": 8,
        "com.tldraw.shape.bookmark": 2,
        "com.tldraw.shape.draw": 4,
        "com.tldraw.shape.embed": 4,
        "com.tldraw.shape.frame": 1,
        "com.tldraw.shape.geo": 11,
        "com.tldraw.shape.group": 0,
        "com.tldraw.shape.highlight": 3,
        "com.tldraw.shape.image": 5,
        "com.tldraw.shape.line": 5,
        "com.tldraw.shape.note": 10,
        "com.tldraw.shape.text": 4,
        "com.tldraw.shape.video": 4,
        "com.tldraw.binding.arrow": 1,
    },
}


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
    """Build a genuine .tldr file for export to someone else's tldraw.

    This is a data-recovery escape hatch, so it must open in a STOCK tldraw --
    one that has never heard of taOS. Two rules follow from that, and both were
    violated by the earlier version of this function:

    1. The envelope is a *file*, not a store snapshot. tldraw's
       parseTldrawJsonFile validates {tldrawFileFormatVersion, schema, records[]}
       and rejects anything else as "notATldrawFile". Emitting the in-memory
       {schema, store{}} shape produced a file tldraw refused to open at all.
    2. Only NATIVE shape types. taos-note/taos-link/taos-image are our own shape
       utils; a stock tldraw has no util for them and cannot render or validate
       them. They were the majority of every real board.

    Lossy by nature: this translates into a foreign format we do not control.
    The lossless copy is the canonical element JSON exported alongside it.
    """
    records: list[dict] = [
        {
            "id": "document:document",
            "typeName": "document",
            "gridSize": 10,
            "name": "",
            "meta": {},
        },
        {
            "id": "page:page",
            "typeName": "page",
            "name": "Page 1",
            "index": "a1",
            "meta": {},
        },
    ]

    # Preserve z-order in the export: tldraw sorts by index, we sort by z_index.
    ordered = sorted(elements, key=lambda e: (e.get("z_index") or 0))
    for i, el in enumerate(ordered):
        records.append(_tldraw_shape_record(el, _index_key(i)))

    return {
        "tldrawFileFormatVersion": _TLDRAW_FILE_FORMAT_VERSION,
        "schema": _TLDRAW_SERIALIZED_SCHEMA,
        "records": records,
    }


def _tldraw_shape_record(el: dict, index: str) -> dict:
    """One canvas element as a native tldraw shape record."""
    # Deliberately NOT passing through a user_shape's literal tldraw_shape blob,
    # even though it would be the highest-fidelity translation available.
    #
    # tldraw validates every record on open and a single failure rejects the
    # WHOLE file with invalidRecords -- so one stale blob would cost the user
    # their entire board, in the one file whose only job is disaster recovery.
    # That risk is not hypothetical here: we declare a fixed schema constant, so
    # tldraw sees records already claiming the current version and runs no
    # migration on a blob actually written by an older tldraw.
    #
    # So this file optimises for "always opens" and the canonical JSON export
    # keeps the lossless copy. Splitting the two jobs is what makes each reliable;
    # asking one file to be both is how you get neither. The raw blob also stays
    # in the DB payload untouched (#2132's append-only rule).
    # Read every field defensively. This is the recovery path, so it runs
    # precisely on the boards whose rows are already ragged, and one raised
    # KeyError/AttributeError aborts the export for the WHOLE project -- the
    # user loses everything because one row was odd. Missing or wrong-typed
    # fields degrade to a placeholder shape instead.
    kind = _str_or(el.get("kind"), "unknown")
    base = {
        "id": f"shape:{el.get('id') or 'unknown'}",
        "typeName": "shape",
        "x": _num_or(el.get("x"), 0.0),
        "y": _num_or(el.get("y"), 0.0),
        "rotation": _num_or(el.get("rotation"), 0.0),
        "index": index,
        "parentId": "page:page",
        "isLocked": False,
        "opacity": 1,
        # taOS provenance rides in meta, which tldraw carries through untouched.
        # It must not go in props: tldraw validates props per shape type and
        # rejects unknown keys.
        "meta": {
            "taos_kind": kind,
            "taos_author_id": el.get("author_id"),
            "taos_author_kind": el.get("author_kind"),
        },
    }

    w = _num_or(el.get("w"), 200.0)
    h = _num_or(el.get("h"), 100.0)
    if kind == "note":
        base["type"] = "note"
        base["props"] = _note_props(_element_text(el))
    elif kind == "text":
        base["type"] = "text"
        base["props"] = _text_props(_element_text(el), w)
    else:
        # Everything else (link, image, mermaid, flowchart, unknown kinds) becomes
        # a labelled rectangle. It is not the original rendering, but it is
        # visible and carries the content as text, which beats vanishing.
        base["type"] = "geo"
        base["props"] = _geo_props(_element_text(el), w, h)
    return base


def _num_or(value, default: float) -> float:
    """Coerce a stored coordinate to a float, or fall back.

    bool is excluded deliberately: it is an int subclass, so True would sail
    through as 1.0 and silently place a shape at the wrong coordinate.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _str_or(value, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _element_text(el: dict) -> str:
    """Best-effort human-readable content for an element, for the export label.

    payload is whatever JSON was stored, which is not guaranteed to be an
    object: a list or bare string decodes truthy and would make .get() raise,
    taking the whole export down over one odd row.
    """
    payload = el.get("payload")
    if isinstance(payload, dict):
        for key in ("text", "title", "label", "source", "url", "caption"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    elif isinstance(payload, str) and payload.strip():
        return payload
    return _str_or(el.get("kind"), "unknown")


def _rich_text(text: str) -> dict:
    """tldraw 4.x stores labels as TipTap rich text, not a plain string."""
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


def _note_props(text: str) -> dict:
    return {
        "color": "yellow",
        "labelColor": "black",
        "size": "m",
        "font": "draw",
        "fontSizeAdjustment": 0,
        "align": "middle",
        "verticalAlign": "middle",
        "growY": 0,
        "url": "",
        "richText": _rich_text(text),
        "scale": 1,
    }


def _text_props(text: str, w: float) -> dict:
    return {
        "color": "black",
        "size": "m",
        "font": "draw",
        "textAlign": "start",
        "w": w or 200,
        "richText": _rich_text(text),
        "scale": 1,
        "autoSize": False,
    }


def _geo_props(text: str, w: float, h: float) -> dict:
    return {
        "geo": "rectangle",
        "dash": "draw",
        "url": "",
        "w": w or 200,
        "h": h or 100,
        "growY": 0,
        "scale": 1,
        "labelColor": "black",
        "color": "black",
        "fill": "none",
        "size": "m",
        "font": "draw",
        "align": "middle",
        "verticalAlign": "middle",
        "richText": _rich_text(text),
    }


# Fractional-index alphabet, matching tldraw's. tldraw validates every index with
# validateIndexKey, so an arbitrary string like "a10" is rejected (a fraction may
# not end in "0"). Generating proper integer keys keeps every shape valid however
# many are on the board -- "a0".."az", then "b00".."bzz", and so on.
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_HEADS = "abcdefghijklmnopqrstuvwxyz"


def _index_key(n: int) -> str:
    """The nth ascending tldraw index key.

    Head "a" means one base62 digit follows, "b" means two, and so on, so the
    keys stay in ascending lexicographic order as they widen.
    """
    for head_ord, head in enumerate(_HEADS):
        capacity = len(_B62) ** (head_ord + 1)
        if n < capacity:
            digits = ""
            for _ in range(head_ord + 1):
                digits = _B62[n % len(_B62)] + digits
                n //= len(_B62)
            return head + digits
        n -= capacity
    # 62 + 62^2 + ... + 62^26 shapes on one board is not reachable in practice;
    # fall back to the last valid key rather than raise during a data export.
    return _HEADS[-1] + _B62[-1] * len(_HEADS)
