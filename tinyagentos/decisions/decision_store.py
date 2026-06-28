"""SQLite-backed store for the Decisions app: the human-in-the-loop inbox.

A decision is a choice an agent needs from the user (single/multi select,
approve/deny, or free text). It queues until answered, so an agent can move on
and pick up the answer later. The branching fields (checkpoint_ref,
parent_decision_id, timeline_id) are reserved in v1 and unused; they exist so
fork-and-replay can be added later without a schema rewrite.
"""

from __future__ import annotations

import json
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

DECISION_TYPES = ("single_select", "multi_select", "approve_deny", "free_text")
PRIORITIES = ("normal", "blocking")

DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id                 TEXT PRIMARY KEY,
    from_agent         TEXT NOT NULL,
    project_id         TEXT,
    user_id            TEXT NOT NULL DEFAULT '',
    question           TEXT NOT NULL,
    type               TEXT NOT NULL,
    options            TEXT NOT NULL DEFAULT '[]',
    context            TEXT NOT NULL DEFAULT '',
    priority           TEXT NOT NULL DEFAULT 'normal',
    status             TEXT NOT NULL DEFAULT 'pending',
    answer             TEXT,
    created_at         REAL NOT NULL,
    answered_at        REAL,
    deadline           REAL,
    checkpoint_ref     TEXT,
    parent_decision_id TEXT,
    timeline_id        TEXT,
    metadata           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, status);
CREATE INDEX IF NOT EXISTS idx_decisions_user ON decisions(user_id, status);
"""

_JSON_FIELDS = ("options", "answer", "metadata")


def _row_to_decision(row, description) -> dict:
    d = dict(zip([c[0] for c in description], row))
    for f in _JSON_FIELDS:
        if d.get(f) is not None:
            d[f] = json.loads(d[f])
    return d


class DecisionStore(BaseStore):
    SCHEMA = DECISIONS_SCHEMA

    async def _post_init(self) -> None:
        # `metadata` was added after the initial decisions ship. Guarded ALTER
        # so existing databases gain it without a destructive migration (SQLite
        # lacks ADD COLUMN IF NOT EXISTS before 3.37). Mirrors board_audit.py.
        cols = {
            row[1]
            for row in await (
                await self._db.execute("PRAGMA table_info(decisions)")
            ).fetchall()
        }
        if "metadata" not in cols:
            await self._db.execute(
                "ALTER TABLE decisions ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
            )
            await self._db.commit()

    async def create(
        self,
        from_agent: str,
        question: str,
        type: str,
        *,
        options: list[dict] | None = None,
        context: str = "",
        priority: str = "normal",
        project_id: str | None = None,
        user_id: str = "",
        deadline: float | None = None,
        parent_decision_id: str | None = None,
        checkpoint_ref: str | None = None,
        timeline_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        if type not in DECISION_TYPES:
            raise ValueError(f"invalid decision type: {type!r}")
        if priority not in PRIORITIES:
            raise ValueError(f"invalid priority: {priority!r}")
        did = new_id("dec")
        now = time.time()
        await self._db.execute(
            """INSERT INTO decisions
               (id, from_agent, project_id, user_id, question, type, options, context,
                priority, status, created_at, deadline, parent_decision_id,
                checkpoint_ref, timeline_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
            (did, from_agent, project_id, user_id, question, type,
             json.dumps(options or []), context, priority, now, deadline,
             parent_decision_id, checkpoint_ref, timeline_id,
             json.dumps(metadata or {})),
        )
        await self._db.commit()
        return await self.get(did)

    async def get(self, decision_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_decision(row, cur.description)

    async def list(
        self,
        *,
        status: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        conds, params = [], []
        if status is not None:
            conds.append("status = ?"); params.append(status)
        if project_id is not None:
            conds.append("project_id = ?"); params.append(project_id)
        if user_id is not None:
            conds.append("user_id = ?"); params.append(user_id)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        # Bound the result set so a long-lived inbox cannot return everything.
        limit = max(1, min(int(limit), 500))
        async with self._db.execute(
            f"SELECT * FROM decisions{where} ORDER BY created_at DESC LIMIT ?",
            [*params, limit],
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_decision(r, desc) for r in rows]

    async def answer(self, decision_id: str, value, answered_by: str) -> dict | None:
        """Record an answer. Returns the updated decision, or None if the
        decision does not exist or is not pending (already answered or
        superseded)."""
        now = time.time()
        ans = json.dumps({"value": value, "answered_by": answered_by, "answered_at": now})
        cur = await self._db.execute(
            """UPDATE decisions
               SET status = 'answered', answer = ?, answered_at = ?
               WHERE id = ? AND status = 'pending'""",
            (ans, now, decision_id),
        )
        await self._db.commit()
        if cur.rowcount != 1:
            return None
        return await self.get(decision_id)

    async def supersede(self, decision_id: str) -> bool:
        """L1 revisit: mark a decision superseded (a later decision replaces it).
        Returns True if a pending/answered decision was superseded."""
        cur = await self._db.execute(
            "UPDATE decisions SET status = 'superseded' WHERE id = ? AND status IN ('pending','answered')",
            (decision_id,),
        )
        await self._db.commit()
        return cur.rowcount == 1
