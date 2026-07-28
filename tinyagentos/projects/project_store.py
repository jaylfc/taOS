from __future__ import annotations

import json
import logging
import sqlite3
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

logger = logging.getLogger(__name__)


class ProjectConflict(ValueError):
    """Raised when a project name or slug collides with an existing one.

    Carries the collided ``field`` ('name' or 'slug') and the ``taken`` value
    so the route can build an actionable 409 with suggestions.
    """

    def __init__(self, field: str, taken: str) -> None:
        self.field = field
        self.taken = taken
        super().__init__(f"{field} already used: {taken}")

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
    lead_member_id TEXT,
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
            "ALTER TABLE project_members ADD COLUMN can_read_canvas INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE project_members ADD COLUMN is_lead INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE projects ADD COLUMN lead_member_id TEXT",
        ):
            try:
                await self._db.execute(col_def)
                await self._db.commit()
            except Exception:
                # Column already exists on fresh installs (created by SCHEMA).
                pass
        # D7 backfill: the exclusive per-project lead moves from the per-member
        # is_lead flag (non-exclusive) to the project's single lead_member_id
        # pointer. Only projects with EXACTLY one flagged member are migrated;
        # any with zero or several flagged members are left NULL so a human picks
        # in the UI (the old flag never promised exclusivity).
        await self._backfill_lead_member_id()

    async def _backfill_lead_member_id(self) -> None:
        rows = await (await self._db.execute(
            "SELECT id FROM projects WHERE lead_member_id IS NULL"
        )).fetchall()
        for (pid,) in rows:
            cur = await self._db.execute(
                "SELECT member_id FROM project_members WHERE project_id = ? AND is_lead = 1",
                (pid,),
            )
            flagged = [r[0] for r in await cur.fetchall()]
            if len(flagged) == 1:
                await self._db.execute(
                    "UPDATE projects SET lead_member_id = ? WHERE id = ?",
                    (flagged[0], pid),
                )
            # Clear the legacy per-member is_lead flag so this backfill runs
            # once — for 0, 1, or many flagged members alike. The epic removed
            # the only writer of is_lead, so without this a lead the owner
            # deliberately cleared (lead_member_id set NULL) would be
            # re-promoted from the stale flag on restart.
            await self._db.execute(
                "UPDATE project_members SET is_lead = 0 WHERE project_id = ?",
                (pid,),
            )
        await self._db.commit()

    async def set_lead(self, project_id: str, member_id: "str | None") -> None:
        """Set (or clear, when member_id is None) the project's exclusive lead.

        The single pointer column makes the one-lead-per-project invariant
        structural: setting a new lead atomically unsets any previous one, and
        there is no partial-update window. A member_id that is not a current
        member of the project raises KeyError (the route maps it to 404).
        """
        p = await self.get_project(project_id)
        if p is None:
            raise KeyError(f"project {project_id!r} not found")
        if member_id is not None:
            member = await self.get_member(project_id, member_id)
            if member is None:
                raise KeyError(f"member {member_id!r} not in project {project_id!r}")
        await self._db.execute(
            "UPDATE projects SET lead_member_id = ? WHERE id = ?",
            (member_id, project_id),
        )
        await self._db.commit()

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
        # Enforce case-insensitive name uniqueness via a query check (not a
        # schema constraint) so existing duplicate names are not destructively
        # rejected on upgrade. A UNIQUE(slug) constraint remains as the
        # backstop for slug collisions caught via IntegrityError below.
        if await self.get_project_by_name(name) is not None:
            raise ProjectConflict("name", name)
        try:
            await self._db.execute(
                """INSERT INTO projects
                   (id, name, slug, description, status, created_by, user_id, created_at, updated_at, settings)
                   VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
                (pid, name, slug, description, created_by, user_id, now, now, json.dumps(settings or {})),
            )
            await self._db.commit()
        except sqlite3.IntegrityError as exc:
            # UNIQUE(slug) is the only schema-level uniqueness a caller can
            # trigger here; the name check above guards name collisions.
            if "slug" in str(exc).lower():
                raise ProjectConflict("slug", slug) from exc
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

    async def get_project_by_name(self, name: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM projects WHERE LOWER(name) = LOWER(?)", (name,)
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
        if member_kind not in ("native", "clone", "human"):
            raise ValueError(f"invalid member_kind: {member_kind}")
        if member_kind == "human":
            # Human members are remote collaborators — no agent lifecycle fields.
            if memory_seed != "none" or source_agent_id is not None:
                logger.warning(
                    "add_member: overriding memory_seed=%r source_agent_id=%r "
                    "for human member %r in project %r",
                    memory_seed, source_agent_id, member_id, project_id,
                )
            memory_seed = "none"
            source_agent_id = None
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
        # Removing the designated lead unsets the pointer so it can never dangle
        # on a member that no longer belongs to the project.
        await self._db.execute(
            "UPDATE projects SET lead_member_id = NULL "
            "WHERE id = ? AND lead_member_id = ?",
            (project_id, member_id),
        )
        await self._db.commit()

    async def set_member_canvas(
        self,
        project_id: str,
        member_id: str,
        can_read: bool = False,
        can_write: bool = False,
    ) -> None:
        """Set a member's per-project canvas flags (best-effort, additive OR).

        Only the flags explicitly passed as True are flipped on; a flag not
        requested is left untouched, so a partial approval (e.g. canvas_read
        only) never clears a flag the member already held.
        """
        sets: list[str] = []
        params: list = []
        if can_read:
            sets.append("can_read_canvas = 1")
        if can_write:
            sets.append("can_edit_canvas = 1")
        if not sets:
            return
        params.extend([project_id, member_id])
        await self._db.execute(
            f"UPDATE project_members SET {', '.join(sets)} "
            "WHERE project_id = ? AND member_id = ?",
            params,
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

    async def get_member(self, project_id: str, member_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM project_members WHERE project_id = ? AND member_id = ?",
            (project_id, member_id),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            keys = [d[0] for d in cur.description]
        return dict(zip(keys, row))

    async def is_project_member(
        self, project_id: str, member_id: str, *, member_kind: str | None = None
    ) -> bool:
        """Return True if *member_id* is a member of *project_id*.

        When *member_kind* is provided, only match members of that kind
        (e.g. ``"human"`` for cross-user collab delegation checks).
        """
        if member_kind is not None:
            async with self._db.execute(
                "SELECT 1 FROM project_members "
                "WHERE project_id = ? AND member_id = ? AND member_kind = ?",
                (project_id, member_id, member_kind),
            ) as cur:
                return (await cur.fetchone()) is not None
        else:
            async with self._db.execute(
                "SELECT 1 FROM project_members "
                "WHERE project_id = ? AND member_id = ?",
                (project_id, member_id),
            ) as cur:
                return (await cur.fetchone()) is not None

    async def get_project_setting(
        self, project_id: str, key: str, default=None
    ):
        """Read a single key from the project's JSON settings dict.

        Returns *default* if the project does not exist, has no settings, or
        the key is absent.
        """
        project = await self.get_project(project_id)
        if project is None:
            return default
        settings = project.get("settings") or {}
        return settings.get(key, default)

    async def set_project_setting(
        self, project_id: str, key: str, value
    ) -> bool:
        """Set a single key in the project's JSON settings dict.

        Returns True if the project was found and updated, False otherwise.
        """
        project = await self.get_project(project_id)
        if project is None:
            return False
        settings = project.get("settings") or {}
        if not isinstance(settings, dict):
            settings = {}
        settings[key] = value
        await self._db.execute(
            "UPDATE projects SET settings = ?, updated_at = ? WHERE id = ?",
            (json.dumps(settings), time.time(), project_id),
        )
        await self._db.commit()
        return True

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
