from __future__ import annotations

"""Store for external-agent consent / auth-request records.

Each record tracks one inbound access request from an external agent.
Pending requests wait for an admin to accept or deny; accepted requests
carry the minted canonical_id and signed JWT token so the agent can poll
and retrieve them.

The state machine is simple: pending → accepted | refused (terminal).
``set_decision`` is atomic — it uses a conditional UPDATE that only
matches rows still in ``pending`` status, so two concurrent approve calls
cannot both win a read-check-then-write race.  ``create`` is atomic the same
way when given a ``pending_cap``: the cap is a condition ON the INSERT, so a
burst of concurrent submissions cannot all pass a stale count and all insert.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore, PendingCapExceeded

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_requests (
    id              TEXT PRIMARY KEY,
    identity_claim  TEXT NOT NULL DEFAULT '',
    framework       TEXT NOT NULL DEFAULT '',
    requested_scopes  TEXT NOT NULL DEFAULT '[]',
    requested_skills  TEXT NOT NULL DEFAULT '[]',
    reason          TEXT NOT NULL DEFAULT '',
    duration_secs   INTEGER,
    project_id      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    canonical_id    TEXT,
    token           TEXT,
    granted_scopes  TEXT,
    created_ts      TEXT NOT NULL,
    decided_ts      TEXT,
    decided_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_requests_status ON auth_requests(status);
CREATE INDEX IF NOT EXISTS idx_auth_requests_identity ON auth_requests(identity_claim, framework, status);
"""

_VALID_DECISION_STATUSES = frozenset({"accepted", "refused"})

# The insert's column list, shared by both ``create`` shapes so the capped and
# uncapped writes can never drift apart on which columns they set.
_CREATE_COLUMNS = (
    "(id, identity_claim, framework, requested_scopes, requested_skills,"
    " reason, duration_secs, project_id, status, created_ts)"
)

# Makes the pending cap atomic with the insert: SQLite evaluates the count and
# performs the insert in ONE statement, so no interleaved caller can slip a row
# in between them. Served by ``idx_auth_requests_identity``.
_CAP_GUARD = (
    "(SELECT COUNT(*) FROM auth_requests "
    " WHERE identity_claim = ? AND framework = ? AND status = 'pending') < ?"
)


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    for field in ("requested_scopes", "requested_skills", "granted_scopes"):
        raw = d.get(field)
        if raw is not None:
            try:
                d[field] = json.loads(raw)
            except (ValueError, TypeError):
                d[field] = []
        else:
            d[field] = None if field == "granted_scopes" else []
    return d


class AuthRequestsStore(BaseStore):
    """Persistent store for external-agent auth requests."""

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
        identity_claim: str,
        framework: str,
        requested_scopes: list[str],
        requested_skills: Optional[list[str]] = None,
        reason: str = "",
        duration_secs: Optional[int] = None,
        project_id: Optional[str] = None,
        pending_cap: Optional[int] = None,
    ) -> dict:
        """Create a new pending auth request. Returns the full record.

        ``pending_cap`` caps how many PENDING rows this (identity_claim,
        framework) pair may hold. The comparison happens inside the INSERT (see
        ``_CAP_GUARD``), so it is atomic with the write: the caller must NOT
        count first and decide, as every request in a concurrent burst would
        read the same pre-insert count, pass, and insert — and this route takes
        no credentials, so that burst is free. Raises ``PendingCapExceeded``
        when the cap is already full.

        ``None`` leaves the store uncapped, for callers that mint exactly one
        row per freshly-deduped identity (invite redemption) rather than
        accepting whatever identity_claim a stranger sends.
        """
        if self._db is None:
            raise RuntimeError("AuthRequestsStore not initialised — call init() first")

        request_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        values = (
            request_id,
            identity_claim,
            framework,
            json.dumps(requested_scopes),
            json.dumps(requested_skills or []),
            reason,
            duration_secs,
            project_id,
            now,
        )

        if pending_cap is None:
            cur = await self._db.execute(
                f"INSERT INTO auth_requests {_CREATE_COLUMNS} "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                values,
            )
        else:
            cur = await self._db.execute(
                f"INSERT INTO auth_requests {_CREATE_COLUMNS} "
                "SELECT ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ? WHERE " + _CAP_GUARD,
                (*values, identity_claim, framework, pending_cap),
            )
        await self._db.commit()

        if cur.rowcount == 0:
            # The guard excluded the row: nothing was written. Read the count
            # back only to describe the refusal — the decision itself was made
            # atomically above.
            raise PendingCapExceeded(
                key=f"{identity_claim}/{framework}",
                cap=pending_cap,
                pending=await self.count_pending_for(identity_claim, framework),
            )

        record = await self.get(request_id)
        if record is None:
            raise RuntimeError(f"auth_request {request_id!r} missing immediately after insert")
        return record

    async def set_decision(
        self,
        request_id: str,
        status: str,
        *,
        canonical_id: Optional[str] = None,
        token: Optional[str] = None,
        granted_scopes: Optional[list[str]] = None,
        decided_by: str,
    ) -> Optional[dict]:
        """Atomically transition a pending request to accepted or refused.

        Returns the updated record on success, or ``None`` if the row was
        already decided (rowcount == 0 from the conditional UPDATE).
        Raises ``ValueError`` for an invalid target status.
        """
        if self._db is None:
            raise RuntimeError("AuthRequestsStore not initialised")
        if status not in _VALID_DECISION_STATUSES:
            raise ValueError(f"status must be 'accepted' or 'refused', got {status!r}")

        now = datetime.now(timezone.utc).isoformat()
        granted_json = json.dumps(granted_scopes) if granted_scopes is not None else None

        cur = await self._db.execute(
            """
            UPDATE auth_requests
               SET status       = ?,
                   canonical_id = ?,
                   token        = ?,
                   granted_scopes = ?,
                   decided_ts   = ?,
                   decided_by   = ?
             WHERE id = ? AND status = 'pending'
            """,
            (status, canonical_id, token, granted_json, now, decided_by, request_id),
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
            raise RuntimeError("AuthRequestsStore not initialised")
        row = await (
            await self._db.execute(
                "SELECT * FROM auth_requests WHERE id = ?", (request_id,)
            )
        ).fetchone()
        return _row_to_dict(row) if row else None

    async def list_pending(self) -> list[dict]:
        """Return all pending auth requests, oldest first."""
        if self._db is None:
            raise RuntimeError("AuthRequestsStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM auth_requests WHERE status = 'pending' ORDER BY created_ts"
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def count_pending_for(self, identity_claim: str, framework: str) -> int:
        """Return the number of pending requests for a given identity+framework pair."""
        if self._db is None:
            raise RuntimeError("AuthRequestsStore not initialised")
        row = await (
            await self._db.execute(
                "SELECT COUNT(*) FROM auth_requests "
                "WHERE identity_claim = ? AND framework = ? AND status = 'pending'",
                (identity_claim, framework),
            )
        ).fetchone()
        return row[0] if row else 0
