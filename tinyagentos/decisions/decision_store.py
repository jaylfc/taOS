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

# Gate decision kinds whose approval carries a privileged grant side effect.
# Must stay in sync with the four ``_apply_*_grant`` handlers in
# routes/decisions.py (execution_gate, delegation_gate, device_pairing,
# app_grant).  NOTE this is intentionally NOT routes.decisions.GATE_DECISION_KINDS
# — that tuple omits ``device_pairing`` (it names only the kinds a device bearer
# or the asking agent may not answer); the backfill below has to cover all four
# guarded handlers, so ``device_pairing`` is listed here explicitly.
GATE_GRANT_KINDS = ("execution_gate", "delegation_gate", "device_pairing", "app_grant")
# Marker literal (must stay == routes.decisions.SERVER_RAISED_KEY).  Defined
# here too because decision_store cannot import from routes (circular); the
# backfill stamps legacy rows and needs the exact key string.
SERVER_RAISED_KEY = "_server_raised"

# Sentinel for "argument not supplied" -- distinguished from an explicit None,
# which means "match NULL project_id" (IS NULL) rather than "no filter".
# A bare ``project_id = ?`` with a NULL parameter matches nothing in SQL, so
# callers that want null-project rows must emit IS NULL instead.
_UNSET = object()


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

        # tsk-mul5pa upgrade backfill: gate decisions persisted before the
        # server-stamped provenance marker landed have no `_server_raised` key,
        # so approving them after upgrade would hit the new early-return in every
        # _apply_*_grant and silently no-op.  Those pre-upgrade rows must be
        # stamped once so a legitimate pending approval still mints.
        #
        # The bound is TIME, not status: without a real one-time gate, a caller
        # could POST a gate-kind decision through the public route (the create
        # path strips the marker but does not restrict `kind`), leave it pending,
        # and have it stamped by the next restart — re-opening the exact caller-
        # minted-privileges hole this PR closes.  So we record the upgrade
        # instant on first run in a persisted marker row, and only ever stamp
        # rows whose `created_at` strictly predates it.  A row created through
        # the public route after deploy has `created_at` >= that instant and is
        # never stamped; the marker makes the whole thing run exactly once.
        # The marker table may not exist yet (first run after this deploy).
        # Check with a guarded PRAGMA, mirroring the metadata-column check above,
        # rather than SELECT-ing a table that is not there yet.
        tables = {
            row[0]
            for row in await (
                await self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='gate_provenance_backfill'"
                )
            ).fetchall()
        }
        marker = None
        if tables:
            marker = await (
                await self._db.execute(
                    "SELECT upgraded_at FROM gate_provenance_backfill WHERE key = ?",
                    ("server_raised",),
                )
            ).fetchone()
        if marker is None:
            upgrade_at = time.time()
            rows = await (
                await self._db.execute(
                    "SELECT id, metadata, created_at FROM decisions "
                    "WHERE status = 'pending' AND created_at < ?",
                    (upgrade_at,),
                )
            ).fetchall()
            to_stamp = []
            for decision_id, metadata_json, _created_at in rows:
                try:
                    meta = json.loads(metadata_json) if metadata_json else {}
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(meta, dict):
                    continue
                if meta.get(SERVER_RAISED_KEY) is True:
                    continue
                if meta.get("kind") not in GATE_GRANT_KINDS:
                    continue
                meta[SERVER_RAISED_KEY] = True
                to_stamp.append((json.dumps(meta), decision_id))
            if to_stamp:
                await self._db.executemany(
                    "UPDATE decisions SET metadata = ? WHERE id = ?", to_stamp
                )
            await self._db.execute(
                "CREATE TABLE IF NOT EXISTS gate_provenance_backfill "
                "(key TEXT PRIMARY KEY, upgraded_at REAL NOT NULL)"
            )
            await self._db.execute(
                "INSERT INTO gate_provenance_backfill (key, upgraded_at) VALUES (?, ?)",
                ("server_raised", upgrade_at),
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
        now = time.time()
        did = await self._insert_with_retry(
            """INSERT INTO decisions
               (id, from_agent, project_id, user_id, question, type, options, context,
                priority, status, created_at, deadline, parent_decision_id,
                checkpoint_ref, timeline_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
            (new_id("dec"), from_agent, project_id, user_id, question, type,
             json.dumps(options or []), context, priority, now, deadline,
             parent_decision_id, checkpoint_ref, timeline_id,
             json.dumps(metadata or {})),
            id_index=0,
            new_id_fn=lambda: new_id("dec"),
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

    async def find_by_metadata(self, key: str, value: str) -> list[dict]:
        """Return decisions whose ``metadata`` JSON has ``metadata[key] == value``.

        Uses SQLite ``json_extract`` (JSON1, bundled in the stdlib SQLite) so
        the match is an exact JSON-key equality, not a lossy substring scan.
        Used by the peer inbox to make ``delegation_status`` handling idempotent
        on ``invite_id``: a retried envelope must not mint a duplicate decision.
        """
        async with self._db.execute(
            "SELECT * FROM decisions "
            "WHERE json_extract(metadata, ?) = ? ORDER BY created_at DESC",
            (f"$.{key}", value),
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_decision(r, desc) for r in rows]

    async def list(
        self,
        *,
        status: str | None = None,
        project_id: str | None | object = _UNSET,
        user_id: str | None = None,
        limit: int = 200,
        from_agent: str | None = None,
    ) -> list[dict]:
        conds, params = [], []
        if status is not None:
            conds.append("status = ?")
            params.append(status)
        if project_id is _UNSET:
            pass  # no project filter
        elif project_id is None:
            conds.append("project_id IS NULL")
        else:
            conds.append("project_id = ?")
            params.append(project_id)
        if user_id is not None:
            conds.append("user_id = ?")
            params.append(user_id)
        if from_agent is not None:
            conds.append("from_agent = ?")
            params.append(from_agent)
        where = ((" WHERE " + " AND ".join(conds)) if conds else "")
        # Bound the result set so a long-lived inbox cannot return everything.
        limit = max(1, min(int(limit), 500))
        async with self._db.execute(
            f"SELECT * FROM decisions{where} ORDER BY created_at DESC LIMIT ?",
            [*params, limit],
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_decision(r, desc) for r in rows]

    async def answer(self, decision_id: str, value, answered_by: str, source: str = "in_app", *, other_value: str | None = None, note: str | None = None) -> dict | None:
        """Record an answer. Returns the updated decision, or None if the
        decision does not exist or is not pending (already answered or
        superseded). The *source* field distinguishes mirrored_from_chat
        from in_app answers for audit/UI purposes."""
        now = time.time()
        ans = json.dumps({"value": value, "answered_by": answered_by, "answered_at": now, "source": source, "other_value": other_value, "note": note})
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
