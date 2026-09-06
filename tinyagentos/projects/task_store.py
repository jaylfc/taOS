from __future__ import annotations

import json
import logging
import time

from typing import TYPE_CHECKING

from tinyagentos.projects.ids import new_id
from tinyagentos.projects.strike_store import StrikeStore
from tinyagentos.projects.tx import ProjectsDBStore

if TYPE_CHECKING:
    from tinyagentos.board_audit import BoardAuditLog
    from tinyagentos.projects.events import ProjectEventBroker
    from tinyagentos.projects.project_store import ProjectStore

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


# Sentinel for update_task's nullable columns (assignee_id, parent_task_id,
# element_id).  For those three, ``None`` is a VALUE -- "clear this to NULL" --
# so "the caller did not mention this field" needs a marker of its own.  The
# non-nullable columns keep the simpler ``None means unchanged`` convention.
_UNCHANGED: object = object()


class ProjectTaskStore(ProjectsDBStore):
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
        """Announce a mutation, once the write behind it has actually landed.

        Every mutation here publishes after its own ``async with self._tx():``
        block, so normally there is no open transaction and this goes straight
        out.  A mutation called from inside another one JOINS that transaction
        instead of nesting, and the join returns without committing -- so the
        event waits for the outermost commit and is dropped if it rolls back
        (see ``tx.after_commit``).  Otherwise the broker would replay a
        transition the database never took.
        """
        if self._broker is None:
            return
        from tinyagentos.projects.events import ProjectEvent
        event = ProjectEvent(kind=kind, payload=payload)

        async def _emit() -> None:
            await self._broker.publish(project_id, event)

        await self._after_commit(_emit)

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
        Like ``_publish`` it waits for the outermost commit when this mutation
        joined an open transaction, so the log cannot keep a transition that
        rolled back.
        """
        if self._audit is None:
            return

        async def _write() -> None:
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
                logger.warning(
                    "board audit record failed for task %s", task_id, exc_info=True
                )

        await self._after_commit(_write)

    async def _post_init(self) -> None:
        # Additive column for project elements (slice 1 of
        # docs/design/projects-nested-elements.md). Existing databases created
        # before the element tag existed get the column added here; fresh
        # installs already have it from SCHEMA. Swallow the duplicate-column
        # error the same way project_store does.
        # DDL runs outside tx(): the connection is in autocommit mode, so each
        # statement commits on its own and the expected duplicate-column error
        # has no transaction left to leak.
        try:
            await self._db.execute(
                "ALTER TABLE project_tasks ADD COLUMN element_id TEXT"
            )
        except Exception:
            pass
        # Created here (not in SCHEMA) so element_id exists first on the
        # migration path; SCHEMA runs before _post_init and would otherwise
        # crash "no such column: element_id" on a pre-element_id table.
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_element "
            "ON project_tasks(project_id, element_id)"
        )
        # Ready-view migration: re-create ready_tasks so it honours the
        # ``blocked-on:<id>`` label mechanism (defect tsk-wkah3z). SQLite's
        # ``CREATE VIEW IF NOT EXISTS`` is a no-op when the view already
        # exists, so databases created before this change keep the old view
        # body and silently keep returning tasks whose blocker is still
        # open. Drop + recreate is safe here: the view is a derived
        # projection of project_tasks, so a rebuild after the drop picks up
        # the new WHERE clause with no data movement.
        async with self._tx():
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
        async with self._tx():
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
        new_task = await self.get_task(tid)
        await self._publish(project_id, "task.created", {"id": new_task["id"], "task": new_task})
        await self._record_audit(tid, "task.created", created_by, None, "open", project_id=project_id)
        return new_task

    async def get_task(self, task_id: str) -> dict | None:
        async with self._read(
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
        async with self._read(sql, params) as cur:
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
        assignee_id: str | None | object = _UNCHANGED,
        parent_task_id: str | None | object = _UNCHANGED,
        element_id: str | None | object = _UNCHANGED,
    ) -> None:
        # Reject generic status transitions from parked: parked is permanent
        # and such a transition would clear claim fields and return the task
        # to the ready pool.  This runs BEFORE `candidates` is built: the
        # tuple captures the status value by copy, so dropping the local name
        # afterwards would leave the original status in the candidate list and
        # the UPDATE would still un-park the task.
        if status is not None:
            existing = await self.get_task(task_id)
            if existing is not None and existing.get("status") == "parked":
                status = None  # skip the status candidate; allow other fields
        candidates = [
            ("title", title, title),
            ("body", body, body),
            ("priority", priority, priority),
            ("labels", labels, json.dumps(labels) if labels is not None else None),
            ("status", status, status),
        ]
        sets: list[str] = []
        params: list = []
        patch: dict = {}
        for col, raw, serialised in candidates:
            if raw is not None:
                sets.append(f"{col} = ?")
                params.append(serialised)
                patch[col] = raw
        # The nullable columns are keyed on the _UNCHANGED sentinel instead, so
        # an explicit None clears them to NULL rather than being read as "field
        # omitted" (tsk-5xq2mw: a clear that is silently dropped answers the
        # caller as if it had been written).
        for col, value in (
            ("assignee_id", assignee_id),
            ("parent_task_id", parent_task_id),
            ("element_id", element_id),
        ):
            if value is _UNCHANGED:
                continue
            sets.append(f"{col} = ?")
            params.append(value)
            patch[col] = value
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
        where = "id = ?"
        if status is not None:
            # Close the read-then-write gap on the parked guard above: if the
            # task was parked between that read and this write, the status edit
            # must not land (it was decided against a stale row).
            where += " AND status != 'parked'"
        async with self._tx():
            cursor = await self._db.execute(
                f"UPDATE project_tasks SET {', '.join(sets)} WHERE {where}", params
            )
            changed = cursor.rowcount == 1
        if not changed:
            # The guard above refused the edit (the task was parked after the
            # pre-read), or the id does not exist.  Nothing committed, so there
            # is nothing to announce: a task.updated here would tell every
            # consumer to apply a patch the database never took.
            return
        existing = await self.get_task(task_id)
        if existing is not None:
            await self._publish(existing["project_id"], "task.updated", {"id": task_id, "patch": patch})

    async def held_task(self, claimer_id: str) -> str | None:
        """Return the id of the active ('claimed') task this agent currently
        holds, or None. Used to enforce one active claim per agent."""
        async with self._read(
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
        async with self._tx():
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
        async with self._tx():
            cursor = await self._db.execute(
                """UPDATE project_tasks
                   SET claimed_by = NULL, claimed_at = NULL, status = 'open', updated_at = ?
                   WHERE id = ? AND claimed_by = ? AND status = 'claimed'""",
                (now, task_id, releaser_id),
            )
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
            if self._strikes is not None:
                try:
                    count = await self._strikes.record_strike(
                        task_id, "dispatch_failed", actor=releaser_id
                    )
                    if count >= StrikeStore.STRIKE_THRESHOLD:
                        # The conditional UPDATE inside park_task is itself the
                        # decision: it only fires while the task is still open
                        # and unclaimed.  A separate pre-read here would leave a
                        # window in which another worker claims the task between
                        # the check and the park, and the park would then
                        # swallow that worker's live claim.
                        await self.park_task(
                            task_id, "system", only_if_unclaimed=True
                        )
                except Exception:
                    logger.warning(
                        "strike recording failed for task %s on release",
                        task_id,
                        exc_info=True,
                    )
        return changed

    async def park_task(
        self, task_id: str, actor: str, *, only_if_unclaimed: bool = False
    ) -> bool:
        """Permanently park a task.

        A parked task is removed from the ready pool permanently.  Unlike
        quarantine there is no un-park operation.  Only acts on a task that is
        not already closed, cancelled, or parked; returns False otherwise.

        ``only_if_unclaimed`` narrows the guard to a task that is still ``open``
        with no claimer, so a caller that must not steal another worker's live
        claim can use this update's row count as the parking decision instead of
        a separate (racy) pre-read.
        """
        now = time.time()
        guard = (
            "status = 'open' AND claimed_by IS NULL"
            if only_if_unclaimed
            else "status NOT IN ('closed', 'cancelled', 'parked')"
        )
        # Park and disown in ONE transaction: parked is terminal, so an owner
        # left on a parked row makes the card look held by an agent that can
        # never release it, and a failure or cancellation between two separate
        # transactions would leave exactly that.
        async with self._tx():
            # Read the row INSIDE the transaction, before the park: that is the
            # real pre-park status for the audit row.  The guard admits any row
            # that is not closed, cancelled or parked -- 'quarantined' included,
            # and quarantine keeps claimed_by -- so inferring the status from
            # the claimer would log a quarantined card as 'claimed'.  Under
            # BEGIN IMMEDIATE this read is not a racy pre-read: no other writer
            # can change the row between it and the UPDATE below.
            existing = await self.get_task(task_id)
            from_status = existing["status"] if existing is not None else None
            cursor = await self._db.execute(
                f"""UPDATE project_tasks
                    SET status = 'parked', updated_at = ?
                    WHERE id = ? AND {guard}""",
                (now, task_id),
            )
            changed = cursor.rowcount == 1
            if changed:
                await self._db.execute(
                    """UPDATE project_tasks
                       SET claimed_by = NULL, claimed_at = NULL
                       WHERE id = ? AND status = 'parked'""",
                    (task_id,),
                )
        if changed:
            if existing is not None:
                await self._publish(
                    existing["project_id"],
                    "task.parked",
                    {"id": task_id, "actor": actor},
                )
            await self._record_audit(
                task_id, "task.parked", actor, from_status, "parked",
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
        async with self._tx():
            if force:
                cursor = await self._db.execute(
                    """UPDATE project_tasks
                       SET status = 'closed', closed_by = ?, closed_at = ?, close_reason = ?, updated_at = ?
                       WHERE id = ? AND status NOT IN ('closed', 'cancelled', 'parked')""",
                    (closed_by, now, reason, now, task_id),
                )
            else:
                cursor = await self._db.execute(
                    """UPDATE project_tasks
                       SET status = 'closed', closed_by = ?, closed_at = ?, close_reason = ?, updated_at = ?
                       WHERE id = ? AND status NOT IN ('closed', 'cancelled', 'parked')
                         AND (claimed_by IS NULL OR claimed_by = ?)""",
                    (closed_by, now, reason, now, task_id, closed_by),
                )
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
        task; returns False otherwise.

        A parked task is refused by that same predicate rather than by one of
        its own: park and close each reject the other's status, so 'parked' and
        'closed' are mutually exclusive and ``status = 'closed'`` is what keeps
        a parked card out of the open pool.  An explicit ``AND status !=
        'parked'`` here would be a dead predicate -- no row can fail it that has
        not already failed the first -- so it is not written.
        """
        now = time.time()
        async with self._tx():
            cursor = await self._db.execute(
                """UPDATE project_tasks
                   SET status = 'open', closed_by = NULL, closed_at = NULL, close_reason = NULL,
                       claimed_by = NULL, claimed_at = NULL, updated_at = ?
                   WHERE id = ? AND status = 'closed'""",
                (now, task_id),
            )
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
        async with self._tx():
            cursor = await self._db.execute(
                """UPDATE project_tasks
                   SET status = 'quarantined', updated_at = ?
                   WHERE id = ? AND status NOT IN ('closed', 'cancelled', 'quarantined')""",
                (now, task_id),
            )
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
        async with self._tx():
            cursor = await self._db.execute(
                """UPDATE project_tasks
                   SET status = 'open', claimed_by = NULL, claimed_at = NULL, updated_at = ?
                   WHERE id = ? AND status = 'quarantined'""",
                (now, task_id),
            )
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
        async with self._tx():
            await self._db.execute(
                """INSERT INTO task_relationships
                   (id, project_id, from_task_id, to_task_id, kind, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rid, project_id, from_task_id, to_task_id, kind, created_by, now),
            )
        await self._publish(project_id, "relationship.added", {"from": from_task_id, "to": to_task_id, "kind": kind})
        return {
            "id": rid, "project_id": project_id, "from_task_id": from_task_id,
            "to_task_id": to_task_id, "kind": kind, "created_by": created_by, "created_at": now,
        }

    async def remove_relationship(self, relationship_id: str) -> None:
        async with self._tx():
            await self._db.execute(
                "DELETE FROM task_relationships WHERE id = ?", (relationship_id,)
            )

    async def list_relationships(
        self,
        task_id: str,
        direction: str = "from",
    ) -> list[dict]:
        if direction not in ("from", "to"):
            raise ValueError(f"invalid direction: {direction}")
        col = "from_task_id" if direction == "from" else "to_task_id"
        async with self._read(
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
        async with self._read(
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
        async with self._read(
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
            async with self._read(
                "SELECT task_id FROM task_comments WHERE id = ?",
                (replies_to_comment_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None or row[0] != task_id:
                raise ValueError("replies_to_comment_id not in this task")
        cid = new_id("cmt")
        now = time.time()
        async with self._tx():
            await self._db.execute(
                """INSERT INTO task_comments
                   (id, task_id, author_id, body, replies_to_comment_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (cid, task_id, author_id, body, replies_to_comment_id, now),
            )
        new_comment = {
            "id": cid, "task_id": task_id, "author_id": author_id, "body": body,
            "replies_to_comment_id": replies_to_comment_id, "created_at": now,
        }
        existing = await self.get_task(task_id)
        if existing is not None:
            await self._publish(existing["project_id"], "comment.added", {"task_id": task_id, "comment": new_comment})
        return new_comment

    async def list_comments(self, task_id: str) -> list[dict]:
        async with self._read(
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
        """Create a checklist item on a task.

        Raises ValueError if the task does not exist: the item's only route to
        a project subscriber is the task's ``project_id``, so a missing task
        leaves nothing to publish under. Resolved before the INSERT so a refusal
        never leaves an orphan row behind.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        cid = new_id("cki")
        now = time.time()
        async with self._tx():
            await self._db.execute(
                """INSERT INTO task_checklist_items
                   (id, task_id, text, done, verified, reported, archived, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, 0, 0, 0, 0, ?, ?, ?)""",
                (cid, task_id, text, created_by, now, now),
            )
        async with self._read(
            "SELECT * FROM task_checklist_items WHERE id = ?", (cid,)
        ) as cur:
            row = await cur.fetchone()
            desc = cur.description
        item = _row_to_checklist_item(row, desc)
        await self._publish(task["project_id"], "checklist.item.created", {"id": item["id"], "text": item["text"], "task_id": task_id})
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
        async with self._read(sql, params) as cur:
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
        async with self._tx():
            await self._db.execute(
                f"UPDATE task_checklist_items SET {', '.join(sets)} WHERE id = ?", params
            )
        return await self.get_checklist_item(item_id)

    async def archive_checklist_item(self, item_id: str) -> dict:
        """Archive a checklist item. Only valid if verified=1 and reported=1.

        Raises ValueError if the item cannot be archived because it lacks
        verification or a report, or because its task is gone — the task
        carries the ``project_id`` that ``checklist.item.archived`` is
        published under, and project subscribers listen at project scope only.
        Resolved before the UPDATE so a refusal leaves the item untouched.
        """
        item = await self.get_checklist_item(item_id)
        if item is None:
            raise ValueError(f"checklist item not found: {item_id}")
        if item["verified"] != 1:
            raise ValueError("item cannot be archived: not verified")
        if item["reported"] != 1:
            raise ValueError("item cannot be archived: not reported")
        task = await self.get_task(item["task_id"])
        if task is None:
            raise ValueError(f"task not found: {item['task_id']}")
        now = time.time()
        async with self._tx():
            # The invariant is enforced by the UPDATE, not only by the reads
            # above it: an update_checklist_item that cleared `verified` or
            # `reported` between the two would otherwise archive an item that
            # no longer satisfies it -- and announce the archival. Matching no
            # row is that race, so it is refused rather than reported as done.
            cursor = await self._db.execute(
                "UPDATE task_checklist_items SET archived = 1, updated_at = ? "
                "WHERE id = ? AND verified = 1 AND reported = 1",
                (now, item_id),
            )
            archived = cursor.rowcount == 1
        if not archived:
            raise ValueError(
                "item cannot be archived: verification or report was withdrawn"
            )
        await self._publish(task["project_id"], "checklist.item.archived", {"id": item_id, "task_id": item["task_id"], "archived": True})
        return await self.get_checklist_item(item_id)

    async def get_checklist_item(self, item_id: str) -> dict | None:
        async with self._read(
            "SELECT * FROM task_checklist_items WHERE id = ?", (item_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            desc = cur.description
            return _row_to_checklist_item(row, desc)
