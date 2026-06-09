from __future__ import annotations

import json
import sqlite3
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

PROJECTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL,
    deleted_at REAL,
    settings TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    member_kind TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    source_agent_id TEXT,
    memory_seed TEXT NOT NULL DEFAULT 'none',
    can_edit_canvas INTEGER NOT NULL DEFAULT 0,
    is_lead INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    PRIMARY KEY (project_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_member ON project_members(member_id);

CREATE TABLE IF NOT EXISTS project_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_activity_project ON project_activity(project_id, created_at DESC);
"""

_JSON_FIELDS = ("settings",)


def _row_to_project(row, description) -> dict:
    keys = [d[0] for d in description]
    p = dict(zip(keys, row))
    for f in _JSON_FIELDS:
        if f in p and p[f] is not None:
            p[f] = json.loads(p[f])
    return p


class ProjectStore(BaseStore):
    SCHEMA = PROJECTS_SCHEMA

    async def _post_init(self) -> None:
        for col_def in (
            "ALTER TABLE project_members ADD COLUMN can_edit_canvas INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE project_members ADD COLUMN is_lead INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
        ):
            try:
                await self._db.execute(col_def)
                await self._db.commit()
            except Exception:
                # Column already exists on fresh installs (created by SCHEMA).
                pass

    async def set_member_lead(self, project_id: str, member_id: str, is_lead: bool) -> None:
        val = 1 if is_lead else 0
        cur = await self._db.execute(
            "UPDATE project_members SET is_lead = ? WHERE project_id = ? AND member_id = ?",
            (val, project_id, member_id),
        )
        await self._db.commit()
        if cur.rowcount == 0:
            raise KeyError(f"member {member_id!r} not in project {project_id!r}")

    async def create_project(
        self,
        name: str,
        slug: str,
        created_by: str,
        description: str = "",
        settings: dict | None = None,
        user_id: str = "",
    ) -> dict:
        pid = new_id("prj")
        now = time.time()
        try:
            await self._db.execute(
                """INSERT INTO projects
                   (id, name, slug, description, status, created_by, user_id, created_at, updated_at, settings)
                   VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
                (pid, name, slug, description, created_by, user_id, now, now, json.dumps(settings or {})),
            )
            await self._db.commit()
        except sqlite3.IntegrityError as exc:
            # UNIQUE(slug) is the only uniqueness constraint a caller can trigger.
            if "slug" in str(exc).lower():
                raise ValueError(f"slug already used: {slug}") from exc
            raise
        return await self.get_project(pid)

    async def get_project(self, project_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_project(row, cur.description)

    async def get_project_by_slug(self, slug: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM projects WHERE slug = ?", (slug,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_project(row, cur.description)

    async def list_projects(self, status: str | None = "active") -> list[dict]:
        """List all projects (admin view). No user_id filter."""
        if status is None:
            sql = "SELECT * FROM projects ORDER BY created_at DESC"
            params: tuple = ()
        else:
            sql = "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC"
            params = (status,)
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_project(r, desc) for r in rows]

    async def list_for_user(self, user_id: str, status: str | None = "active") -> list[dict]:
        """List projects owned by a specific user (member view).

        Returns an empty list for an empty user_id — legacy rows (user_id='')
        are never owned by any real user and must only be visible to admins
        via list_projects().
        """
        if not user_id:
            return []
        if status is None:
            sql = "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC"
            params: tuple = (user_id,)
        else:
            sql = "SELECT * FROM projects WHERE user_id = ? AND status = ? ORDER BY created_at DESC"
            params = (user_id, status)
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_project(r, desc) for r in rows]

    async def update_project(
        self,
        project_id: str,
        name: str | None = None,
        description: str | None = None,
        settings: dict | None = None,
    ) -> None:
        sets: list[str] = []
        params: list = []
        if name is not None:
            sets.append("name = ?"); params.append(name)
        if description is not None:
            sets.append("description = ?"); params.append(description)
        if settings is not None:
            sets.append("settings = ?"); params.append(json.dumps(settings))
        if not sets:
            return
        sets.append("updated_at = ?"); params.append(time.time())
        params.append(project_id)
        await self._db.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()

    async def set_status(self, project_id: str, status: str) -> None:
        if status not in ("active", "archived", "deleted"):
            raise ValueError(f"invalid status: {status}")
        now = time.time()
        col_map = {"archived": "archived_at", "deleted": "deleted_at"}
        extra_col = col_map.get(status)
        if extra_col:
            await self._db.execute(
                f"UPDATE projects SET status = ?, updated_at = ?, {extra_col} = ? WHERE id = ?",
                (status, now, now, project_id),
            )
        else:
            await self._db.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, project_id),
            )
        await self._db.commit()

    async def add_member(
        self,
        project_id: str,
        member_id: str,
        member_kind: str,
        role: str = "member",
        source_agent_id: str | None = None,
        memory_seed: str = "none",
    ) -> None:
        if member_kind not in ("native", "clone"):
            raise ValueError(f"invalid member_kind: {member_kind}")
        if memory_seed not in ("none", "snapshot", "empty"):
            raise ValueError(f"invalid memory_seed: {memory_seed}")
        await self._db.execute(
            """INSERT INTO project_members
               (project_id, member_id, member_kind, role, source_agent_id, memory_seed, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, member_id) DO NOTHING""",
            (project_id, member_id, member_kind, role, source_agent_id, memory_seed, time.time()),
        )
        await self._db.commit()

    async def remove_member(self, project_id: str, member_id: str) -> None:
        await self._db.execute(
            "DELETE FROM project_members WHERE project_id = ? AND member_id = ?",
            (project_id, member_id),
        )
        await self._db.commit()

    async def list_members(self, project_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM project_members WHERE project_id = ? ORDER BY added_at ASC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
            keys = [d[0] for d in cur.description]
        return [dict(zip(keys, r)) for r in rows]

    async def log_activity(
        self,
        project_id: str,
        actor_id: str,
        kind: str,
        payload: dict | None = None,
    ) -> None:
        await self._db.execute(
            """INSERT INTO project_activity
               (project_id, actor_id, kind, payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, actor_id, kind, json.dumps(payload or {}), time.time()),
        )
        await self._db.commit()

    async def list_activity(self, project_id: str, limit: int = 100) -> list[dict]:
        limit = max(1, min(limit, 500))
        async with self._db.execute(
            """SELECT * FROM project_activity
               WHERE project_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            keys = [d[0] for d in cur.description]
        out: list[dict] = []
        for r in rows:
            d = dict(zip(keys, r))
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
            out.append(d)
        return out
