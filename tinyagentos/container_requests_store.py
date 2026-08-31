from __future__ import annotations

"""SQLite-backed store for agent container provisioning requests (P1).

An agent submits a request to provision a container for hosting a project.
The request record tracks the state machine:

    requested -> approved            (policy auto-approved: under quota)
    requested -> pending-approval    (over quota: needs manual review)
    requested -> pending-approval    (over threshold: escalated to Decisions)

``approved`` is the P2 trigger: the provisioning executor creates the incus
container and transitions to ``provisioned``. ``failed`` captures provisioning
errors. Terminal states are ``provisioned`` and ``failed``; all others are
non-terminal (an active/in-progress request that still consumes quota).
"""

import json
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

# Open set of states. Stored as strings so they are inspectable in raw SQL and
# can be added to without a schema migration in early slices.
STATES = ("requested", "approved", "pending-approval", "provisioned", "failed")
TERMINAL_STATES = ("provisioned", "failed")

CONTAINER_REQUESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS container_requests (
    id               TEXT PRIMARY KEY,
    canonical_id     TEXT NOT NULL,
    image            TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'requested',
    reason           TEXT NOT NULL DEFAULT '',
    config_json      TEXT NOT NULL DEFAULT '{}',
    decision_id      TEXT,
    container_name   TEXT,
    error            TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crq_canonical ON container_requests(canonical_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_crq_status ON container_requests(status);
"""

_JSON_FIELDS = ("config_json",)


def _row_to_request(row, description) -> dict:
    d = dict(zip([c[0] for c in description], row))
    for f in _JSON_FIELDS:
        if d.get(f) is not None:
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                d[f] = {}
    return d


class ContainerRequestStore(BaseStore):
    SCHEMA = CONTAINER_REQUESTS_SCHEMA

    async def create(
        self,
        canonical_id: str,
        *,
        image: str = "",
        reason: str = "",
        config: dict | None = None,
    ) -> dict:
        """Insert a new request in the ``requested`` state and return the row."""
        if canonical_id is None or not canonical_id.strip():
            raise ValueError("canonical_id is required")
        crq_id = new_id("crq")
        now = time.time()
        config_str = json.dumps(config or {})
        await self._db.execute(
            """INSERT INTO container_requests
               (id, canonical_id, image, status, reason, config_json, created_at, updated_at)
               VALUES (?, ?, ?, 'requested', ?, ?, ?, ?)""",
            (crq_id, canonical_id, image, reason, config_str, now, now),
        )
        await self._db.commit()
        return await self.get(crq_id)

    async def get(self, request_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM container_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_request(row, cur.description)

    async def list(
        self,
        *,
        canonical_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        conds, params = [], []
        if canonical_id is not None:
            conds.append("canonical_id = ?")
            params.append(canonical_id)
        if status is not None:
            conds.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        limit = max(1, min(int(limit), 500))
        async with self._db.execute(
            f"SELECT * FROM container_requests{where} ORDER BY created_at DESC LIMIT ?",
            [*params, limit],
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_request(r, desc) for r in rows]

    async def count_active_for_agent(self, canonical_id: str) -> int:
        """Count non-terminal requests for an agent (quota-consuming)."""
        async with self._db.execute(
            """SELECT COUNT(*) FROM container_requests
               WHERE canonical_id = ? AND status NOT IN ('provisioned', 'failed')""",
            (canonical_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def set_status(
        self,
        request_id: str,
        status: str,
        *,
        container_name: str | None = None,
        error: str | None = None,
        decision_id: str | None = None,
    ) -> dict | None:
        if status not in STATES:
            raise ValueError(f"invalid status: {status!r}")
        now = time.time()
        updates: list[str] = ["status = ?", "updated_at = ?"]
        params: list = [status, now]
        if container_name is not None:
            updates.append("container_name = ?")
            params.append(container_name)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if decision_id is not None:
            updates.append("decision_id = ?")
            params.append(decision_id)
        params.append(request_id)
        cur = await self._db.execute(
            f"UPDATE container_requests SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await self._db.commit()
        if cur.rowcount != 1:
            return None
        return await self.get(request_id)

    async def link_decision(self, request_id: str, decision_id: str) -> dict | None:
        now = time.time()
        cur = await self._db.execute(
            "UPDATE container_requests SET decision_id = ?, updated_at = ? WHERE id = ?",
            (decision_id, now, request_id),
        )
        await self._db.commit()
        if cur.rowcount != 1:
            return None
        return await self.get(request_id)
