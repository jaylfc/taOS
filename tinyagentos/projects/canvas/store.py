from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

if TYPE_CHECKING:
    from tinyagentos.projects.events import ProjectEventBroker


CANVAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_canvas_elements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    author_kind TEXT NOT NULL,
    author_id TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    w REAL NOT NULL,
    h REAL NOT NULL,
    rotation REAL NOT NULL DEFAULT 0,
    z_index INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
);
CREATE INDEX IF NOT EXISTS idx_canvas_project ON project_canvas_elements(project_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_canvas_updated ON project_canvas_elements(project_id, updated_at);
"""

_CANVAS_JSON_FIELDS = ("payload",)
_VALID_KINDS = {"note", "link", "image", "user_shape"}
_AGENT_ALLOWED_KINDS = {"note", "link", "image"}


class CanvasPermissionError(PermissionError):
    """Raised when an agent without can_edit_canvas tries to update/delete."""


def _row_to_element(row, description) -> dict:
    keys = [d[0] for d in description]
    e = dict(zip(keys, row))
    for f in _CANVAS_JSON_FIELDS:
        if f in e and e[f] is not None:
            e[f] = json.loads(e[f])
    return e


class ProjectCanvasStore(BaseStore):
    SCHEMA = CANVAS_SCHEMA

    def __init__(self, db_path, *, broker: "ProjectEventBroker | None" = None) -> None:
        super().__init__(db_path)
        self._broker = broker

    async def _publish(self, project_id: str, kind: str, payload: dict) -> None:
        if self._broker is not None:
            from tinyagentos.projects.events import ProjectEvent
            await self._broker.publish(project_id, ProjectEvent(kind=kind, payload=payload))

    async def get_element(
        self, element_id: str, *, project_id: str | None = None
    ) -> dict | None:
        if project_id is None:
            sql = "SELECT * FROM project_canvas_elements WHERE id = ?"
            args: tuple = (element_id,)
        else:
            sql = (
                "SELECT * FROM project_canvas_elements "
                "WHERE id = ? AND project_id = ?"
            )
            args = (element_id, project_id)
        async with self._db.execute(sql, args) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_element(row, cur.description)

    async def add_element(
        self,
        *,
        project_id: str,
        element: dict,
        author_kind: str,
        author_id: str,
    ) -> dict:
        kind = element.get("kind")
        if kind not in _VALID_KINDS:
            raise ValueError(f"invalid kind: {kind}")
        if author_kind == "agent" and kind not in _AGENT_ALLOWED_KINDS:
            raise ValueError(f"agents may not emit kind={kind}")
        if author_kind not in ("user", "agent"):
            raise ValueError(f"invalid author_kind: {author_kind}")
        eid = element.get("id") or new_id("cve")
        now = time.time()
        await self._db.execute(
            """INSERT INTO project_canvas_elements
               (id, project_id, kind, author_kind, author_id,
                x, y, w, h, rotation, z_index, payload, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, project_id, kind, author_kind, author_id,
                float(element["x"]), float(element["y"]),
                float(element["w"]), float(element["h"]),
                float(element.get("rotation", 0)),
                int(element.get("z_index", 0)),
                json.dumps(element.get("payload") or {}),
                now, now,
            ),
        )
        await self._db.commit()
        new_el = await self.get_element(eid)
        await self._publish(project_id, "canvas.element_added", {"element": new_el})
        return new_el

    async def list_elements(self, project_id: str) -> list[dict]:
        async with self._db.execute(
            """SELECT * FROM project_canvas_elements
               WHERE project_id = ? AND deleted_at IS NULL
               ORDER BY z_index ASC, created_at ASC""",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_element(r, desc) for r in rows]

    async def _check_edit_permission(
        self, project_id: str, author_kind: str, author_id: str
    ) -> None:
        if author_kind == "user":
            return
        if author_kind != "agent":
            raise ValueError(f"invalid author_kind: {author_kind}")
        async with self._db.execute(
            "SELECT can_edit_canvas FROM project_members "
            "WHERE project_id = ? AND member_id = ?",
            (project_id, author_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None or not row[0]:
            raise CanvasPermissionError(
                f"agent {author_id} has no can_edit_canvas on project {project_id}"
            )

    async def update_element(
        self,
        *,
        project_id: str,
        element_id: str,
        patch: dict,
        author_kind: str,
        author_id: str,
    ) -> dict:
        await self._check_edit_permission(project_id, author_kind, author_id)
        sets: list[str] = []
        params: list = []
        for col in ("x", "y", "w", "h", "rotation", "z_index"):
            if col in patch:
                sets.append(f"{col} = ?")
                params.append(patch[col])
        if "payload" in patch:
            sets.append("payload = ?")
            params.append(json.dumps(patch["payload"]))
        if not sets:
            existing = await self.get_element(element_id, project_id=project_id)
            if existing is None:
                raise ValueError(f"element not found: {element_id}")
            return existing
        sets.append("updated_at = ?"); params.append(time.time())
        params.append(element_id)
        params.append(project_id)
        await self._db.execute(
            f"UPDATE project_canvas_elements SET {', '.join(sets)} "
            f"WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
            params,
        )
        await self._db.commit()
        updated = await self.get_element(element_id, project_id=project_id)
        if updated is None:
            raise ValueError(f"element not found: {element_id}")
        await self._publish(project_id, "canvas.element_updated", {"element": updated})
        return updated

    async def delete_element(
        self,
        *,
        project_id: str,
        element_id: str,
        author_kind: str,
        author_id: str,
    ) -> None:
        await self._check_edit_permission(project_id, author_kind, author_id)
        now = time.time()
        cur = await self._db.execute(
            """UPDATE project_canvas_elements
               SET deleted_at = ?, updated_at = ?
               WHERE id = ? AND project_id = ? AND deleted_at IS NULL""",
            (now, now, element_id, project_id),
        )
        await self._db.commit()
        if cur.rowcount == 1:
            await self._publish(
                project_id, "canvas.element_deleted",
                {"element_id": element_id},
            )
