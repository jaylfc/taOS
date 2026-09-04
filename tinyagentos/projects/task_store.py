from __future__ import annotations

import json
import logging
import time

from typing import TYPE_CHECKING

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

if TYPE_CHECKING:
    from tinyagentos.board_audit import BoardAuditLog
    from tinyagentos.projects.events import ProjectEventBroker
    from tinyagentos.projects.project_store import ProjectStore
    from tinyagentos.projects.strike_store import StrikeStore

logger = logging.getLogger(__name__)

# Ancestor-walk depth cap for get_task_context — mirrors the cycle guard in
# routes/projects.py's parent-chain check.
_MAX_ANCESTRY_DEPTH = 50

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
    element_id TEXT,
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
  )
  AND NOT EXISTS (
      SELECT 1 FROM json_each(t.labels) je
      JOIN project_tasks bt
        ON 'blocked-on:' || bt.id = je.value
       AND bt.project_id = t.project_id
      WHERE bt.status NOT IN ('closed', 'cancelled')
  );

CREATE TABLE IF NOT EXISTS task_checklist_items (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES project_tasks(id),
    text TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 0,
    reported INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checklist_task
  ON task_checklist_items(task_id, archived, done);
"""

_TASK_JSON_FIELDS = ("labels",)


def _row_to_task(row, description) -> dict:
    keys = [d[0] for d in description]
    t = dict(zip(keys, row))
    for f in _TASK_JSON_FIELDS:
        if f in t and t[f] is not None:
            t[f] = json.loads(t[f])
    return t


def _row_to_checklist_item(row, description) -> dict:
    keys = [d[0] for d in description]
    c = dict(zip(keys, row))
    c["done"] = bool(c.get("done", 0))
    c["verified"] = bool(c.get("verified", 0))
    c["reported"] = bool(c.get("reported", 0))
    c["archived"] = bool(c.get("archived", 0))
    return c


# Sentinels for update_task's element_id:
#   _ELEMENT_UNCHANGED -> leave the task's element tag untouched (PATCH omitted)
#   _ELEMENT_CLEAR     -> explicitly clear the tag to NULL ("none" sentinel)
_ELEMENT_UNCHANGED: object = object()
_ELEMENT_CLEAR: object = object()


class ProjectTaskStore(BaseStore):
    SCHEMA = TASK_SCHEMA

    def __init__(
        self,
        db_path,
        *,
        broker: "ProjectEventBroker | None" = None,
        audit: "BoardAuditLog | None" = None,
        project_store: "ProjectStore | None" = None,
        strikes: "StrikeStore | None" = None,
    ) -> None:
        super().__init__(db_path)
        self._broker = broker
        self._audit = audit
        self._project_store = project_store
        self._strikes = strikes

    async def _publish(self, project_id: str, kind: str, payload: dict) -> None:
        if self._broker is not None:
            from tinyagentos.projects.events import ProjectEvent
            await self._broker.publish(project_id, ProjectEvent(kind=kind, payload=payload))

    async def _record_audit(
        self,
        task_id: str,
        event: str,
        actor: str,
        from_status: str | None,
        to_status: str | None,
        project_id: str = "",
    ) -> None:
        """Append a status transition to the board audit log (best effort).

        The audit log lives in its own store; a failure to record must never
        roll back or break the task mutation that already committed. project_id
        is recorded so the project-scoped activity feed never crosses projects.
        """
        if self._audit is None:
            return
        try:
            await self._audit.record(
                task_id=task_id,
                event=event,
                actor=actor,
                from_status=from_status,
                to_status=to_status,
                project_id=project_id,
            )
        except Exception:
            logger.warning("board audit record failed for task %s", task_id, exc_info=True)

    async def _post_init(self) -> None:
        # Additive column for project elements (slice 1 of
        # docs/design/projects-nested-elements.md). Existing databases created
        # before the element tag existed get the column added here; fresh
        # installs already have it from SCHEMA. Swallow the duplicate-column
        # error the same way project_store does.
        try:
            await self._db.execute(
                "ALTER TABLE project_tasks ADD COLUMN element_id TEXT"
            )
            await self._db.commit()
        except Exception:
            pass
        # Created here (not in SCHEMA) so element_id exists first on the
        # migration path; SCHEMA runs before _post_init and would otherwise
        # crash "no such column: element_id" on a pre-element_id table.
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_element "
            "ON project_tasks(project_id, element_id)"
        )
        await self._db.commit()
        # Ready-view migration: re-create ready_tasks so it honours the
        # ``blocked-on:<id>`` label mechanism (defect tsk-wkah3z). SQLite's
        # ``CREATE VIEW IF NOT EXISTS`` is a no-op when the view already
        # exists, so databases created before this change keep the old view
        # body and silently keep returning tasks whose blocker is still
        # open. Drop + recreate is safe here: the view is a derived
        # projection of project_tasks, so a rebuild after the drop picks up
        # the new WHERE clause with no data movement.
        await self._db.execute("DROP VIEW IF EXISTS ready_tasks")
        await self._db.execute(
            "CREATE VIEW ready_tasks AS "
            "SELECT t.* FROM project_tasks t "
            "WHERE t.status = 'open' "
            "AND t.claimed_by IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM task_relationships r "
            "JOIN project_tasks bt ON bt.id = r.to_task_id "
            "WHERE r.from_task_id = t.id "
            "AND r.kind = 'blocks' "
            "AND bt.status NOT IN ('closed', 'cancelled')"
            ") "
            "AND NOT EXISTS ("
            "SELECT 1 FROM json_each(t.labels) je "
            "JOIN project_tasks bt ON 'blocked-on:' || bt.id = je.value "
            "AND bt.project_id = t.project_id "
            "WHERE bt.status NOT IN ('closed', 'cancelled')"
            ")"
        )
        await self._db.commit()
        # Add created_by column for checklist items (defect tsk-6xymzj).
        # Fresh installs already have it from SCHEMA. SQLite cannot ADD COLUMN NOT
        # NULL without a default, so migrated databases get a NULLABLE column
        # here; legacy rows stay NULL -- intended.
        import sqlite3
        async with self._db.execute("PRAGMA table_info(task_checklist_items)") as cur:
            columns = await cur.fetchall()
            column_names = [col[1] for col in columns]
            if "created_by" not in column_names:
                try:
                    await self._db.execute(
                        "ALTER TABLE task_checklist_items ADD COLUMN created_by TEXT"
                    )
                    await self._db.commit()
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e):
                        raise

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
        element_id: str | None = None,
    ) -> dict:
        tid = new_id("tsk")
        now = time.time()
        await self._db.execute(
            """INSERT INTO project_tasks
               (id, project_id, parent_task_id, title, body, status, priority, labels,
                assignee_id, element_id, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid, project_id, parent_task_id, title, body, priority,
                json.dumps(labels or []), assignee_id, element_id, created_by, now, now,
            ),
        )
        await self._db.commit()
        new_task = await self.get_task(tid)
        await self._publish(project_id, "task.created", {"id": new_task["id"], "task": new_task})
        await self._record_audit(tid, "task.created", created_by, None, "open", project_id=project_id)
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
        element_id: str | None = None,
    ) -> list[dict]:
        conds = ["project_id = ?"]
        params: list = [project_id]
        if status is not None:
            conds.append("status = ?")
            params.append(status)
        if parent_task_id is not None:
            conds.append("parent_task_id = ?")
            params.append(parent_task_id)
        # element_id convention (mirrors the API surface):
        #   None      -> no element filter (project-level + every element)
        #   "none"    -> only untagged (project-level) tasks
        #   <real id> -> only tasks tagged with that element
        if element_id is not None:
            if element_id == "none":
                conds.append("element_id IS NULL")
            else:
                conds.append("element_id = ?")
                params.append(element_id)
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
        status: str | None = None,
        assignee_id: str | None = None,
        parent_task_id: str | None = None,
        element_id: object = _ELEMENT_UNCHANGED,
    ) -> None:
        candidates = [
            ("title", title, title),
            ("body", body, body),
            ("priority", priority, priority),
            ("labels", labels, json.dumps(labels) if labels is not None else None),
            ("status", status, status),
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
        # element_id is handled separately so None can mean "unchanged" while an
        # explicit clear sets the tag to NULL.
        if element_id is not _ELEMENT_UNCHANGED:
            sets.append("element_id = ?")
            if element_id is _ELEMENT_CLEAR:
                params.append(None)
                patch["element_id"] = None
            else:
                params.append(element_id)
                patch["element_id"] = element_id
        if not sets:
            return
        # A generic edit back to 'open' must also clear the claimer (as the
        # dedicated to-open transitions do): claim_task requires
        # claimed_by IS NULL, so a stale claimer leaves the card unclaimable.
        if status == "open":
            sets.append("claimed_by = ?"); params.append(None); patch["claimed_by"] = None
            sets.append("claimed_at = ?"); params.append(None); patch["claimed_at"] = None
        sets.append("updated_at = ?"); params.append(time.time())
        params.append(task_id)
        await self._db.execute(
            f"UPDATE project_tasks SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        existing = await self.get_task(task_id)
        if existing is not None:
            await self._publish(existing["project_id"], "task.updated", {"id": task_id, "patch": patch})

    async def held_task(self, claimer_id: str) -> str | None:
        """Return the id of the active ('claimed') task this agent currently
        holds, or None. Used to enforce one active claim per agent."""
        async with self._db.execute(
            "SELECT id FROM project_tasks WHERE claimed_by = ? AND status = 'claimed' LIMIT 1",
            (claimer_id,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def claim_task(self, task_id: str, claimer_id: str) -> bool:
        now = time.time()
        # Flow protection: an agent must complete (close) or release a task
        # before claiming another. The NOT EXISTS guard makes the one-active-
        # claim rule atomic, so concurrent claims by the same agent can't race
        # past it.
        cursor = await self._db.execute(
            """UPDATE project_tasks
               SET claimed_by = ?, claimed_at = ?, status = 'claimed', updated_at = ?
               WHERE id = ? AND claimed_by IS NULL AND status = 'open'
                 AND NOT EXISTS (
                     SELECT 1 FROM project_tasks held
                     WHERE held.claimed_by = ? AND held.status = 'claimed'
                 )""",
            (claimer_id, now, now, task_id, claimer_id),
        )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            existing = await self.get_task(task_id)
            if existing is not None:
                await self._publish(existing["project_id"], "task.claimed", {"id": task_id, "claimed_by": claimer_id})
            await self._record_audit(
                task_id, "task.claimed", claimer_id, "open", "claimed",
                project_id=existing["project_id"] if existing else "",
            )
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
            await self._record_audit(
                task_id, "task.released", releaser_id, "claimed", "open",
                project_id=existing["project_id"] if existing else "",
            )
        return changed

    async def close_task(
        self,
        task_id: str,
        closed_by: str,
        reason: str | None = None,
        *,
        force: bool = False,
    ) -> bool:
        now = time.time()
        if force:
            cursor = await self._db.execute(
                """UPDATE project_tasks
                   SET status = 'closed', closed_by = ?, closed_at = ?, close_reason = ?, updated_at = ?
                   WHERE id = ? AND status NOT IN ('closed', 'cancelled')""",
                (closed_by, now, reason, now, task_id),
            )
        else:
            cursor = await self._db.execute(
                """UPDATE project_tasks
                   SET status = 'closed', closed_by = ?, closed_at = ?, close_reason = ?, updated_at = ?
                   WHERE id = ? AND status NOT IN ('closed', 'cancelled')
                     AND (claimed_by IS NULL OR claimed_by = ?)""",
                (closed_by, now, reason, now, task_id, closed_by),
            )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            existing = await self.get_task(task_id)
            if existing is not None:
                await self._publish(existing["project_id"], "task.closed", {"id": task_id, "closed_by": closed_by})
            # A closed card can never be re-landed, so any lingering strikes
            # are stale garbage that would make the dispatch lanes redo landed
            # work.  Clear them automatically (best-effort; the close itself
            # has already committed).
            if self._strikes is not None:
                try:
                    await self._strikes.clear_strikes(task_id)
                except Exception:
                    logger.warning("clear_strikes failed for task %s on close", task_id, exc_info=True)
            # Derive the pre-close status race-free from the committed row rather
            # than a separate pre-read (which would have a TOCTOU gap). close does
            # not clear claimed_by, so a set claimer means it was 'claimed'.
            from_status = "claimed" if existing and existing.get("claimed_by") else "open"
            await self._record_audit(
                task_id, "task.closed", closed_by, from_status, "closed",
                project_id=existing["project_id"] if existing else "",
            )
        return changed

    async def reopen_task(self, task_id: str, reopened_by: str) -> bool:
        """Undo a close: a closed task returns to the open pool (claimer stays
        cleared, so a free agent can pick it up again). Only acts on a closed
        task; returns False otherwise."""
        now = time.time()
        cursor = await self._db.execute(
            """UPDATE project_tasks
               SET status = 'open', closed_by = NULL, closed_at = NULL, close_reason = NULL,
                   claimed_by = NULL, claimed_at = NULL, updated_at = ?
               WHERE id = ? AND status = 'closed'""",
            (now, task_id),
        )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            existing = await self.get_task(task_id)
            if existing is not None:
                await self._publish(existing["project_id"], "task.reopened", {"id": task_id, "reopened_by": reopened_by})
            await self._record_audit(
                task_id, "task.reopened", reopened_by, "closed", "open",
                project_id=existing["project_id"] if existing else "",
            )
        return changed

    async def quarantine_task(self, task_id: str, actor: str) -> bool:
        """Move a task into the ``quarantined`` status.

        A quarantined card is visible on the board (distinct column) but is
        removed from the ready pool -- the fleet will not pick it up until a
        lead explicitly un-quarantines it.  Only acts on a task that is not
        already closed/cancelled; returns False otherwise.
        """
        now = time.time()
        cursor = await self._db.execute(
            """UPDATE project_tasks
               SET status = 'quarantined', updated_at = ?
               WHERE id = ? AND status NOT IN ('closed', 'cancelled', 'quarantined')""",
            (now, task_id),
        )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            existing = await self.get_task(task_id)
            if existing is not None:
                strike_count = (
                    await self._strikes.count_strikes(task_id)
                    if self._strikes is not None
                    else 0
                )
                latest_strike = (
                    await self._strikes.latest(task_id)
                    if self._strikes is not None
                    else None
                )
                await self._publish(
                    existing["project_id"],
                    "task.quarantined",
                    {
                        "id": task_id,
                        "actor": actor,
                        "strike_count": strike_count,
                        "latest_strike": latest_strike,
                    },
                )
            # Derive the pre-quarantine status race-free from the committed row
            # rather than a separate pre-read (which would have a TOCTOU gap).
            # quarantine does not clear claimed_by, so a set claimer means it was
            # 'claimed' (cf. close_task's derivation).
            from_status = "claimed" if existing and existing.get("claimed_by") else "open"
            await self._record_audit(
                task_id, "task.quarantined", actor, from_status, "quarantined",
                project_id=existing["project_id"] if existing else "",
            )
        return changed

    async def unquarantine_task(self, task_id: str, actor: str) -> bool:
        """Return a quarantined task to the open pool and clear its strikes.

        This is the explicit un-quarantine / retry action: the card re-enters
        the ready pool so the fleet may pick it up again.  Strikes are cleared
        so a fresh failure count starts from zero, and the claimer clears (as
        in ``reopen_task``) -- claim_task requires ``claimed_by IS NULL``, so
        a stale claimer would leave the card open but unclaimable forever.
        Only acts on a quarantined task; returns False otherwise.
        """
        now = time.time()
        cursor = await self._db.execute(
            """UPDATE project_tasks
               SET status = 'open', claimed_by = NULL, claimed_at = NULL, updated_at = ?
               WHERE id = ? AND status = 'quarantined'""",
            (now, task_id),
        )
        await self._db.commit()
        changed = cursor.rowcount == 1
        if changed:
            if self._strikes is not None:
                try:
                    await self._strikes.clear_strikes(task_id)
                except Exception:
                    logger.warning("clear_strikes failed for task %s on unquarantine", task_id, exc_info=True)
            existing = await self.get_task(task_id)
            if existing is not None:
                await self._publish(
                    existing["project_id"],
                    "task.unquarantined",
                    {"id": task_id, "actor": actor},
                )
            await self._record_audit(
                task_id, "task.unquarantined", actor, "quarantined", "open",
                project_id=existing["project_id"] if existing else "",
            )
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

    async def list_ready_tasks(
        self, project_id: str, limit: int = 50, element_id: str | None = None
    ) -> list[dict]:
        # Clamp to [1, 500]. The floor stops ``limit=0`` / negative inputs from
        # being silently widened to ``unbounded`` or the default 50, and the
        # cap protects the route from a caller asking for an unreasonable
        # window (defect tsk-wkah3z).
        limit = max(1, min(limit, 500))
        conds = ["project_id = ?"]
        params: list = [project_id]
        if element_id is not None:
            if element_id == "none":
                conds.append("element_id IS NULL")
            else:
                conds.append("element_id = ?")
                params.append(element_id)
        params.append(limit)
        async with self._db.execute(
            f"""SELECT * FROM ready_tasks
                WHERE {' AND '.join(conds)}
                ORDER BY priority DESC, created_at ASC
                LIMIT ?""",
            params,
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_task(r, desc) for r in rows]

    async def list_ready_tasks_for_assignee(self, assignee_id: str, limit: int = 5) -> list[dict]:
        """Ready tasks (open, unclaimed, unblocked) assigned to *assignee_id*,
        across all projects. Mirrors list_ready_tasks's ordering but scopes by
        assignee instead of project - used by the agent heartbeat loop to find
        an idle agent's next task without a per-project scan."""
        limit = max(1, min(limit, 200))
        async with self._db.execute(
            """SELECT * FROM ready_tasks
               WHERE assignee_id = ?
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (assignee_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_task(r, desc) for r in rows]

    async def get_task_context(self, task_id: str) -> dict:
        """Relational context for a task: its goal (project + parent-task
        ancestry) and what's blocking it.

        Ancestry is ordered root -> leaf (the task's immediate parent last),
        excluding the task itself. The walk is capped at
        _MAX_ANCESTRY_DEPTH and guards against cycles via a visited set.

        Blockers are read via `direction="from"` on this task: a `blocks`
        relationship is stored as (from=dependent, to=blocker) — see
        ready_tasks / test_closing_blocker_unblocks_ready_view, where a task
        with an outbound `blocks` edge to an open task is excluded from the
        ready set until that target closes.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")

        ancestry: list[dict] = []
        seen = {task_id}
        parent_id = task.get("parent_task_id")
        depth = 0
        while parent_id and depth < _MAX_ANCESTRY_DEPTH:
            if parent_id in seen:
                break
            seen.add(parent_id)
            parent = await self.get_task(parent_id)
            if parent is None:
                break
            ancestry.append({
                "id": parent["id"], "title": parent["title"], "status": parent["status"],
            })
            parent_id = parent.get("parent_task_id")
            depth += 1
        ancestry.reverse()

        project_id = task["project_id"]
        project: dict = {"id": project_id, "name": None, "description": None}
        if self._project_store is not None:
            proj = await self._project_store.get_project(project_id)
            if proj is not None:
                project = {
                    "id": proj["id"],
                    "name": proj.get("name"),
                    "description": proj.get("description", ""),
                }

        outbound = await self.list_relationships(task_id, direction="from")
        blockers: list[dict] = []
        for rel in outbound:
            if rel.get("kind") != "blocks":
                continue
            blocker = await self.get_task(rel["to_task_id"])
            if blocker is not None:
                blockers.append({"id": blocker["id"], "title": blocker["title"], "status": blocker["status"]})
        is_blocked = any(b["status"] not in ("closed", "cancelled") for b in blockers)

        return {
            "project": project,
            "ancestry": ancestry,
            "blockers": blockers,
            "is_blocked": is_blocked,
        }

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

    # ------------------------------------------------------------------ checklist items

    async def create_checklist_item(
        self,
        task_id: str,
        text: str,
        created_by: str,
    ) -> dict:
        cid = new_id("cki")
        now = time.time()
        await self._db.execute(
            """INSERT INTO task_checklist_items
               (id, task_id, text, done, verified, reported, archived, created_by, created_at, updated_at)
               VALUES (?, ?, ?, 0, 0, 0, 0, ?, ?, ?)""",
            (cid, task_id, text, created_by, now, now),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT * FROM task_checklist_items WHERE id = ?", (cid,)
        )
        row = await cur.fetchone()
        desc = cur.description
        item = _row_to_checklist_item(row, desc)
        task = await self.get_task(task_id)
        project_id = task["project_id"] if task is not None else ""
        await self._publish(project_id, "checklist.item.created", {"id": item["id"], "text": item["text"], "task_id": task_id})
        return item

    async def list_checklist_items(
        self,
        task_id: str,
        *,
        include_archived: bool = False,
    ) -> list[dict]:
        conds = ["task_id = ?"]
        params: list = [task_id]
        if not include_archived:
            conds.append("archived = 0")
        sql = f"SELECT * FROM task_checklist_items WHERE {' AND '.join(conds)} ORDER BY created_at ASC"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_checklist_item(r, desc) for r in rows]

    async def update_checklist_item(
        self,
        item_id: str,
        *,
        done: bool | None = None,
        verified: bool | None = None,
        reported: bool | None = None,
    ) -> dict | None:
        now = time.time()
        candidates: list[tuple[str, object]] = []
        if done is not None:
            candidates.append(("done", 1 if done else 0))
        if verified is not None:
            candidates.append(("verified", 1 if verified else 0))
        if reported is not None:
            candidates.append(("reported", 1 if reported else 0))
        if not candidates:
            return await self.get_checklist_item(item_id)
        sets: list[str] = []
        params: list = []
        for col, val in candidates:
            sets.append(f"{col} = ?")
            params.append(val)
        sets.append("updated_at = ?")
        params.append(now)
        params.append(item_id)
        await self._db.execute(
            f"UPDATE task_checklist_items SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_checklist_item(item_id)

    async def archive_checklist_item(self, item_id: str) -> dict:
        """Archive a checklist item. Only valid if verified=1 and reported=1.

        Raises ValueError if the item cannot be archived because it lacks
        verification or a report.
        """
        item = await self.get_checklist_item(item_id)
        if item is None:
            raise ValueError(f"checklist item not found: {item_id}")
        if item["verified"] != 1:
            raise ValueError("item cannot be archived: not verified")
        if item["reported"] != 1:
            raise ValueError("item cannot be archived: not reported")
        now = time.time()
        await self._db.execute(
            "UPDATE task_checklist_items SET archived = 1, updated_at = ? WHERE id = ?",
            (now, item_id),
        )
        await self._db.commit()
        task = await self.get_task(item["task_id"])
        project_id = task["project_id"] if task is not None else ""
        await self._publish(project_id, "checklist.item.archived", {"id": item_id, "task_id": item["task_id"], "archived": True})
        return await self.get_checklist_item(item_id)

    async def get_checklist_item(self, item_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM task_checklist_items WHERE id = ?", (item_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            desc = cur.description
            return _row_to_checklist_item(row, desc)
