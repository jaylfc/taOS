from __future__ import annotations

import json
import time

from typing import TYPE_CHECKING

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

if TYPE_CHECKING:
    from tinyagentos.projects.events import ProjectEventBroker

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_task_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    priority INTEGER NOT NULL DEFAULT 0,
    labels TEXT NOT NULL DEFAULT '[]',
    assignee_id TEXT,
    claimed_by TEXT,
    claimed_at REAL,
    closed_at REAL,
    closed_by TEXT,
    close_reason TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON project_tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON project_tasks(parent_task_id);

CREATE TABLE IF NOT EXISTS task_relationships (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    from_task_id TEXT NOT NULL,
    to_task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (from_task_id, to_task_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON task_relationships(from_task_id);
CREATE INDEX IF NOT EXISTS idx_rel_to ON task_relationships(to_task_id);

CREATE TABLE IF NOT EXISTS task_comments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    replies_to_comment_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON task_comments(task_id, created_at);

CREATE VIEW IF NOT EXISTS ready_tasks AS
SELECT t.*
FROM project_tasks t
WHERE t.status = 'open'
  AND t.claimed_by IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM task_relationships r
      JOIN project_tasks bt ON bt.id = r.to_task_id
      WHERE r.from_task_id = t.id
        AND r.kind = 'blocks'
        AND bt.status NOT IN ('closed', 'cancelled')
  );
"""

_TASK_JSON_FIELDS = ("labels",)


def _row_to_task(row, description) -> dict:
    keys = [d[0] for d in description]
    t = dict(zip(keys, row))
    for f in _TASK_JSON_FIELDS:
        if f in t and t[f] is not None:
            t[f] = json.loads(t[f])
    return t


class ProjectTaskStore(BaseStore):
    SCHEMA = TASK_SCHEMA

    def __init__(self, db_path, *, broker: "ProjectEventBroker | None" = None) -> None:
        super().__init__(db_path)
        self._broker = broker

    async def _publish(self, project_id: str, kind: str, payload: dict) -> None:
        if self._broker is not None:
            from tinyagentos.projects.events import ProjectEvent
            await self._broker.publish(project_id, ProjectEvent(kind=kind, payload=payload))

    async def create_task(
        self,
        project_id: str,
        title: str,
        created_by: str,
        body: str = "",
        priority: int = 0,
        labels: list[str] | None = None,
        assignee_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> dict:
        tid = new_id("tsk")
        now = time.time()
        await self._db.execute(
            """INSERT INTO project_tasks
               (id, project_id, parent_task_id, title, body, status, priority, labels,
                assignee_id, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
            (
                tid, project_id, parent_task_id, title, body, priority,
                json.dumps(labels or []), assignee_id, created_by, now, now,
            ),
        )
        await self._db.commit()
        new_task = await self.get_task(tid)
        await self._publish(project_id, "task.created", {"id": new_task["id"], "task": new_task})
        return new_task

    async def get_task(self, task_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM project_tasks WHERE id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_task(row, cur.description)

    async def list_tasks(
        self,
        project_id: str,
        status: str | None = None,
        parent_task_id: str | None = None,
    ) -> list[dict]:
        conds = ["project_id = ?"]
        params: list = [project_id]
        if status is not None:
            conds.append("status = ?")
            params.append(status)
        if parent_task_id is not None:
            conds.append("parent_task_id = ?")
            params.append(parent_task_id)
        sql = f"SELECT * FROM project_tasks WHERE {' AND '.join(conds)} ORDER BY created_at ASC"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_task(r, desc) for r in rows]

    async def update_task(
        self,
        task_id: str,
        title: str | None = None,
        body: str | None = None,
        priority: int | None = None,
        labels: list[str] | None = None,
        assignee_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> None:
        candidates = [
            ("title", title, title),
            ("body", body, body),
            ("priority", priority, priority),
            ("labels", labels, json.dumps(labels) if labels is not None else None),
            ("assignee_id", assignee_id, assignee_id),
            ("parent_task_id", parent_task_id, parent_task_id),
        ]
        sets: list[str] = []
        params: list = []
        patch: dict = {}
        for col, raw, serialised in candidates:
            if raw is not None:
                sets.append(f"{col} = ?")
                params.append(serialised)
                patch[col] = raw
        if not sets:
            return
        sets.append("updated_at = ?"); params.append(time.time())
        params.append(task_id)
        await self._db.execute(
            f"UPDATE project_tasks SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        existing = await self.get_task(task_id)
        if existing is not None:
            await self._publish(existing["project_id"], "task.updated", {"id": task_id, "patch": patch})

    async def claim_task(self, task_id: str, claimer_id: str) -> bool:
        now = time.time()
        cursor = await self._db.execute(
            """UPDATE project_tasks
               SET claimed_by = ?, claimed_at = ?, status = 'claimed', updated_at = ?
               WHERE id = ? AND claimed_by IS NULL AND status = 'open'""",
            (claimer_id, now, now, task_id),
        )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            existing = await self.get_task(task_id)
            if existing is not None:
                await self._publish(existing["project_id"], "task.claimed", {"id": task_id, "claimed_by": claimer_id})
        return changed

    async def release_task(self, task_id: str, releaser_id: str) -> bool:
        now = time.time()
        cursor = await self._db.execute(
            """UPDATE project_tasks
               SET claimed_by = NULL, claimed_at = NULL, status = 'open', updated_at = ?
               WHERE id = ? AND claimed_by = ? AND status = 'claimed'""",
            (now, task_id, releaser_id),
        )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            existing = await self.get_task(task_id)
            if existing is not None:
                await self._publish(
                    existing["project_id"],
                    "task.released",
                    {"id": task_id, "releaser_id": releaser_id},
                )
        return changed

    async def close_task(
        self,
        task_id: str,
        closed_by: str,
        reason: str | None = None,
    ) -> bool:
        now = time.time()
        cursor = await self._db.execute(
            """UPDATE project_tasks
               SET status = 'closed', closed_by = ?, closed_at = ?, close_reason = ?, updated_at = ?
               WHERE id = ? AND status NOT IN ('closed', 'cancelled')""",
            (closed_by, now, reason, now, task_id),
        )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            existing = await self.get_task(task_id)
            if existing is not None:
                await self._publish(existing["project_id"], "task.closed", {"id": task_id, "closed_by": closed_by})
        return changed

    async def add_relationship(
        self,
        project_id: str,
        from_task_id: str,
        to_task_id: str,
        kind: str,
        created_by: str,
    ) -> dict:
        if kind not in ("blocks", "relates_to", "duplicates", "supersedes"):
            raise ValueError(f"invalid relationship kind: {kind}")
        for tid in (from_task_id, to_task_id):
            t = await self.get_task(tid)
            if t is None or t["project_id"] != project_id:
                raise ValueError(f"task not in project: {tid}")
        rid = new_id("rel")
        now = time.time()
        await self._db.execute(
            """INSERT INTO task_relationships
               (id, project_id, from_task_id, to_task_id, kind, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rid, project_id, from_task_id, to_task_id, kind, created_by, now),
        )
        await self._db.commit()
        await self._publish(project_id, "relationship.added", {"from": from_task_id, "to": to_task_id, "kind": kind})
        return {
            "id": rid, "project_id": project_id, "from_task_id": from_task_id,
            "to_task_id": to_task_id, "kind": kind, "created_by": created_by, "created_at": now,
        }

    async def remove_relationship(self, relationship_id: str) -> None:
        await self._db.execute(
            "DELETE FROM task_relationships WHERE id = ?", (relationship_id,)
        )
        await self._db.commit()

    async def list_relationships(
        self,
        task_id: str,
        direction: str = "from",
    ) -> list[dict]:
        if direction not in ("from", "to"):
            raise ValueError(f"invalid direction: {direction}")
        col = "from_task_id" if direction == "from" else "to_task_id"
        async with self._db.execute(
            f"SELECT * FROM task_relationships WHERE {col} = ? ORDER BY created_at ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
            keys = [d[0] for d in cur.description]
        return [dict(zip(keys, r)) for r in rows]

    async def list_ready_tasks(self, project_id: str, limit: int = 50) -> list[dict]:
        limit = max(1, min(limit, 200))
        async with self._db.execute(
            """SELECT * FROM ready_tasks
               WHERE project_id = ?
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_task(r, desc) for r in rows]

    async def add_comment(
        self,
        task_id: str,
        author_id: str,
        body: str,
        replies_to_comment_id: str | None = None,
    ) -> dict:
        if replies_to_comment_id is not None:
            async with self._db.execute(
                "SELECT task_id FROM task_comments WHERE id = ?",
                (replies_to_comment_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None or row[0] != task_id:
                raise ValueError("replies_to_comment_id not in this task")
        cid = new_id("cmt")
        now = time.time()
        await self._db.execute(
            """INSERT INTO task_comments
               (id, task_id, author_id, body, replies_to_comment_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cid, task_id, author_id, body, replies_to_comment_id, now),
        )
        await self._db.commit()
        new_comment = {
            "id": cid, "task_id": task_id, "author_id": author_id, "body": body,
            "replies_to_comment_id": replies_to_comment_id, "created_at": now,
        }
        existing = await self.get_task(task_id)
        if existing is not None:
            await self._publish(existing["project_id"], "comment.added", {"task_id": task_id, "comment": new_comment})
        return new_comment

    async def list_comments(self, task_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
            keys = [d[0] for d in cur.description]
        return [dict(zip(keys, r)) for r in rows]
