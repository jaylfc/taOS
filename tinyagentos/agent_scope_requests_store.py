from __future__ import annotations

"""Store for scope-request records raised by ALREADY-REGISTERED agents.

Unlike ``auth_requests`` (which mints a NEW identity on approval), a
scope-request targets an EXISTING canonical_id: an agent that already holds an
active registry identity asks the owner/admin for ADDITIONAL scope grants on
that same identity.  Approval writes grants to the existing canonical_id; it
never registers a second identity.

The state machine mirrors ``auth_requests``: pending → accepted | refused
(terminal).  ``set_decision`` is atomic — a conditional UPDATE that only
matches rows still in ``pending`` status, so two concurrent approvals cannot
both win a read-check-then-write race.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_scope_requests (
    id                TEXT PRIMARY KEY,
    canonical_id      TEXT NOT NULL,
    requested_scopes  TEXT NOT NULL DEFAULT '[]',
    project_id        TEXT,
    reason            TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'pending',
    granted_scopes    TEXT,
    created_ts        TEXT NOT NULL,
    decided_ts        TEXT,
    decided_by        TEXT
);
CREATE INDEX IF NOT EXISTS idx_scope_requests_status ON agent_scope_requests(status);
CREATE INDEX IF NOT EXISTS idx_scope_requests_canonical ON agent_scope_requests(canonical_id, status);
"""

_VALID_DECISION_STATUSES = frozenset({"accepted", "refused"})


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    for field in ("requested_scopes", "granted_scopes"):
        raw = d.get(field)
        if raw is not None:
            try:
                d[field] = json.loads(raw)
            except (ValueError, TypeError):
                d[field] = []
        else:
            d[field] = None if field == "granted_scopes" else []
    return d


class AgentScopeRequestsStore(BaseStore):
    """Persistent store for existing-agent scope-request records."""

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        canonical_id: str,
        requested_scopes: list[str],
        project_id: Optional[str] = None,
        reason: str = "",
    ) -> dict:
        """Create a new pending scope request. Returns the full record."""
        if self._db is None:
            raise RuntimeError("AgentScopeRequestsStore not initialised — call init() first")

        request_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """
            INSERT INTO agent_scope_requests
                (id, canonical_id, requested_scopes, project_id, reason, status, created_ts)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                request_id,
                canonical_id,
                json.dumps(requested_scopes),
                project_id,
                reason,
                now,
            ),
        )
        await self._db.commit()
        record = await self.get(request_id)
        if record is None:
            raise RuntimeError(f"scope_request {request_id!r} missing immediately after insert")
        return record

    async def set_decision(
        self,
        request_id: str,
        status: str,
        *,
        granted_scopes: Optional[list[str]] = None,
        decided_by: str,
    ) -> Optional[dict]:
        """Atomically transition a pending request to accepted or refused.

        Returns the updated record on success, or ``None`` if the row was
        already decided (rowcount == 0 from the conditional UPDATE).
        Raises ``ValueError`` for an invalid target status.
        """
        if self._db is None:
            raise RuntimeError("AgentScopeRequestsStore not initialised")
        if status not in _VALID_DECISION_STATUSES:
            raise ValueError(f"status must be 'accepted' or 'refused', got {status!r}")

        now = datetime.now(timezone.utc).isoformat()
        granted_json = json.dumps(granted_scopes) if granted_scopes is not None else None

        cur = await self._db.execute(
            """
            UPDATE agent_scope_requests
               SET status         = ?,
                   granted_scopes = ?,
                   decided_ts     = ?,
                   decided_by     = ?
             WHERE id = ? AND status = 'pending'
            """,
            (status, granted_json, now, decided_by, request_id),
        )
        await self._db.commit()

        if cur.rowcount == 0:
            # Already decided — caller should treat as conflict.
            return None

        return await self.get(request_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, request_id: str) -> Optional[dict]:
        """Return the record for *request_id*, or ``None``."""
        if self._db is None:
            raise RuntimeError("AgentScopeRequestsStore not initialised")
        row = await (
            await self._db.execute(
                "SELECT * FROM agent_scope_requests WHERE id = ?", (request_id,)
            )
        ).fetchone()
        return _row_to_dict(row) if row else None

    async def list_pending(self, canonical_id: Optional[str] = None) -> list[dict]:
        """Return pending scope requests, oldest first.

        Optional ``canonical_id`` narrows to a single agent.
        """
        if self._db is None:
            raise RuntimeError("AgentScopeRequestsStore not initialised")
        if canonical_id is not None:
            cursor = await self._db.execute(
                "SELECT * FROM agent_scope_requests "
                "WHERE status = 'pending' AND canonical_id = ? ORDER BY created_ts",
                (canonical_id,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM agent_scope_requests WHERE status = 'pending' ORDER BY created_ts"
            )
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def count_pending_for(self, canonical_id: str) -> int:
        """Return the number of pending requests for a given canonical_id."""
        if self._db is None:
            raise RuntimeError("AgentScopeRequestsStore not initialised")
        row = await (
            await self._db.execute(
                "SELECT COUNT(*) FROM agent_scope_requests "
                "WHERE canonical_id = ? AND status = 'pending'",
                (canonical_id,),
            )
        ).fetchone()
        return row[0] if row else 0
