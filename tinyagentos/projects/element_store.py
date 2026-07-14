"""Project element store.

A project element is a first-class, typed, optionally-owned subdivision of a
project (see docs/design/projects-nested-elements.md). Elements own a tag on
tasks and canvas items; a NULL tag means project-level. The element record
itself lives in ``projects.db`` alongside the project and task stores.

This module is slice 1 of the design: schema, CRUD, and the counts query that
backs the element overview grid. Assignment routing (slice 5) and group/promote
(slice 6/7) build on top of it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

# Element slugs follow the project slug rules.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

ELEMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_elements (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'generic',
    description TEXT NOT NULL DEFAULT '',
    assignee_id TEXT,
    settings    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    archived_at REAL,
    UNIQUE (project_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_elements_project ON project_elements(project_id);
"""

_JSON_FIELDS = ("settings",)


def slugify_element_name(name: str) -> str:
    """Best-effort slug from a human name, matching project slug rules."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "element"


def validate_element_slug(slug: str) -> str:
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError("slug must match ^[a-z0-9][a-z0-9_-]{0,62}$")
    return slug


def _row_to_element(row, description) -> dict:
    keys = [d[0] for d in description]
    e = dict(zip(keys, row))
    for f in _JSON_FIELDS:
        if f in e and e[f] is not None:
            e[f] = json.loads(e[f])
    return e


class ProjectElementStore(BaseStore):
    SCHEMA = ELEMENT_SCHEMA

    async def get_element(self, element_id: str) -> "dict | None":
        async with self._db.execute(
            "SELECT * FROM project_elements WHERE id = ?", (element_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_element(row, cur.description)

    async def get_element_by_slug(self, project_id: str, slug: str) -> "dict | None":
        async with self._db.execute(
            "SELECT * FROM project_elements WHERE project_id = ? AND slug = ?",
            (project_id, slug),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_element(row, cur.description)

    async def create_element(
        self,
        project_id: str,
        name: str,
        slug: str | None = None,
        type: str = "generic",
        description: str = "",
        assignee_id: "str | None" = None,
        settings: "dict | None" = None,
    ) -> dict:
        if slug is None or slug == "":
            slug = slugify_element_name(name)
        validate_element_slug(slug)
        eid = new_id("elm")
        now = time.time()
        try:
            await self._db.execute(
                """INSERT INTO project_elements
                   (id, project_id, name, slug, type, description, assignee_id,
                    settings, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    eid, project_id, name, slug, type, description, assignee_id,
                    json.dumps(settings or {}), now, now,
                ),
            )
            await self._db.commit()
        except sqlite3.IntegrityError as exc:
            # UNIQUE(project_id, slug) is the only uniqueness a caller can trigger.
            if "slug" in str(exc).lower() or "unique" in str(exc).lower():
                await self._db.rollback()
                raise ValueError(f"element slug already used in project: {slug}") from exc
            await self._db.rollback()
            raise
        return await self.get_element(eid)

    async def update_element(
        self,
        element_id: str,
        name: "str | None" = None,
        type: "str | None" = None,
        description: "str | None" = None,
        assignee_id: "str | None" = None,
        settings: "dict | None" = None,
    ) -> "dict | None":
        candidates = [
            ("name", name, name),
            ("type", type, type),
            ("description", description, description),
            ("assignee_id", assignee_id, assignee_id),
            ("settings", settings, json.dumps(settings) if settings is not None else None),
        ]
        sets: list[str] = []
        params: list = []
        for col, raw, serialised in candidates:
            if raw is not None:
                sets.append(f"{col} = ?")
                params.append(serialised)
        if not sets:
            return await self.get_element(element_id)
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(element_id)
        await self._db.execute(
            f"UPDATE project_elements SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_element(element_id)

    async def archive_element(self, element_id: str) -> bool:
        now = time.time()
        cur = await self._db.execute(
            "UPDATE project_elements SET archived_at = ?, updated_at = ? WHERE id = ?",
            (now, now, element_id),
        )
        await self._db.commit()
        return cur.rowcount == 1

    async def count_element_items(self, project_id: str, element_id: str) -> dict:
        """Counts of items currently tagged with this element.

        ``canvas_items`` tolerates the missing column gracefully: the canvas
        ``element_id`` tag is added in slice 4, so until then the count is 0.
        Once that column exists, the same query returns the real count with no
        code change here.
        """
        total_tasks = 0
        open_tasks = 0
        try:
            total_tasks = await self._count(
                "SELECT COUNT(*) FROM project_tasks WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )
            open_tasks = await self._count(
                "SELECT COUNT(*) FROM project_tasks WHERE project_id = ? AND element_id = ? AND status = 'open'",
                (project_id, element_id),
            )
        except sqlite3.OperationalError:
            # project_tasks may not be initialized in the shared db yet.
            total_tasks = 0
            open_tasks = 0
        canvas_items = 0
        try:
            canvas_items = await self._count(
                "SELECT COUNT(*) FROM project_canvas_elements WHERE project_id = ? AND element_id = ?",
                (project_id, element_id),
            )
        except sqlite3.OperationalError:
            # element_id column on project_canvas_elements not yet present (slice 4).
            canvas_items = 0
        return {
            "open_tasks": open_tasks,
            "total_tasks": total_tasks,
            "canvas_items": canvas_items,
        }

    async def list_elements(self, project_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM project_elements WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        out: list[dict] = []
        for r in rows:
            e = _row_to_element(r, desc)
            counts = await self.count_element_items(project_id, e["id"])
            e["open_tasks"] = counts["open_tasks"]
            e["total_tasks"] = counts["total_tasks"]
            e["canvas_items"] = counts["canvas_items"]
            out.append(e)
        return out

    async def delete_element(self, element_id: str, untag: bool = False) -> None:
        if untag:
            await self._db.execute(
                "UPDATE project_tasks SET element_id = NULL WHERE element_id = ?",
                (element_id,),
            )
            # Canvas untag (slice 4). Tolerates a not-yet-initialized canvas
            # table the same way count_element_items does, so deleting an
            # element never fails just because the canvas store has not run.
            try:
                await self._db.execute(
                    "UPDATE project_canvas_elements SET element_id = NULL WHERE element_id = ?",
                    (element_id,),
                )
            except sqlite3.OperationalError:
                pass
        await self._db.execute(
            "DELETE FROM project_elements WHERE id = ?", (element_id,)
        )
        await self._db.commit()

    async def _count(self, sql: str, params: tuple) -> int:
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0
