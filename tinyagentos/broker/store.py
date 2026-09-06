"""SQLite-backed grant ledger for the Secrets Broker.

Two tables: ``broker_grants`` (the per-(agent, provider) lifecycle record)
and ``broker_virtual_creds`` (virtual tokens minted for http-proxy grants).
Providers are code-seeded from ``providers.py`` and are NOT a DB table in P0
(simpler; the registry is small and edited in code).

Timestamps are epoch seconds (REAL). All SQL is parameterised. WAL +
busy_timeout mirror the LiteLLM keystore so a concurrent reader does not
block on a writer.
"""
from __future__ import annotations

import json
import secrets as _secrets
import time
import uuid
from pathlib import Path

from tinyagentos.base_store import BaseStore

# Virtual-token prefix, mirroring the LiteLLM keystore convention.
_TOKEN_PREFIX = "sk-taos-secret-"

# status values: pending, active, denied, revoked, expired

BROKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_grants (
    id                  TEXT PRIMARY KEY,
    agent_identity      TEXT NOT NULL,
    provider_id         TEXT NOT NULL,
    scopes              TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL,
    granted_at          REAL,
    expires_at          REAL,
    created_request_id  TEXT,
    note                TEXT,
    last_used_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_broker_grants_lookup
    ON broker_grants(agent_identity, provider_id, status);

CREATE TABLE IF NOT EXISTS broker_virtual_creds (
    token       TEXT PRIMARY KEY,
    grant_id    TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_broker_virtual_creds_grant
    ON broker_virtual_creds(grant_id);
"""

_GRANT_COLS = (
    "id",
    "agent_identity",
    "provider_id",
    "scopes",
    "status",
    "granted_at",
    "expires_at",
    "created_request_id",
    "note",
    "last_used_at",
)


def _grant_row_to_dict(row) -> dict:
    d = dict(zip(_GRANT_COLS, row))
    try:
        d["scopes"] = json.loads(d["scopes"]) if d["scopes"] else []
    except (ValueError, TypeError):
        d["scopes"] = []
    return d


class BrokerStore(BaseStore):
    SCHEMA = BROKER_SCHEMA

    async def init(self) -> None:
        await super().init()
        # WAL + busy_timeout for cross-process safety (matches the keystore).
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.commit()

    # --- grants ---------------------------------------------------------

    async def create_pending_grant(
        self,
        agent_identity: str,
        provider_id: str,
        scopes: list[str] | None,
        request_id: str | None,
        note: str | None,
    ) -> str:
        """Insert a pending grant (no granted_at/expires_at yet). Returns its id."""
        grant_id = uuid.uuid4().hex
        scopes_json = json.dumps(list(scopes or []))
        await self._db.execute(
            "INSERT INTO broker_grants "
            "(id, agent_identity, provider_id, scopes, status, granted_at, "
            "expires_at, created_request_id, note, last_used_at) "
            "VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, NULL)",
            (grant_id, agent_identity, provider_id, scopes_json, request_id, note),
        )
        await self._db.commit()
        return grant_id

    async def set_grant_status(
        self,
        grant_id: str,
        status: str,
        *,
        expires_at: float | None = None,
        granted_at: float | None = None,
    ) -> bool:
        """Update a grant's status (and optionally granted_at/expires_at).

        granted_at/expires_at are only written when provided so a status flip
        (e.g. -> expired) does not clobber the original approval timestamps.
        """
        sets = ["status = ?"]
        params: list = [status]
        if granted_at is not None:
            sets.append("granted_at = ?")
            params.append(granted_at)
        if expires_at is not None:
            sets.append("expires_at = ?")
            params.append(expires_at)
        params.append(grant_id)
        cur = await self._db.execute(
            f"UPDATE broker_grants SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def get_grant(self, grant_id: str) -> dict | None:
        async with self._db.execute(
            f"SELECT {', '.join(_GRANT_COLS)} FROM broker_grants WHERE id = ?",
            (grant_id,),
        ) as cur:
            row = await cur.fetchone()
        return _grant_row_to_dict(row) if row else None

    async def find_active_grant(
        self, agent_identity: str, provider_id: str, now: float
    ) -> dict | None:
        """Return the active, unexpired grant for (agent, provider), or None."""
        async with self._db.execute(
            f"SELECT {', '.join(_GRANT_COLS)} FROM broker_grants "
            "WHERE agent_identity = ? AND provider_id = ? AND status = 'active' "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY granted_at DESC LIMIT 1",
            (agent_identity, provider_id, now),
        ) as cur:
            row = await cur.fetchone()
        return _grant_row_to_dict(row) if row else None

    async def list_grants(
        self, agent_identity: str | None = None, status: str | None = None
    ) -> list[dict]:
        sql = f"SELECT {', '.join(_GRANT_COLS)} FROM broker_grants"
        clauses: list[str] = []
        params: list = []
        if agent_identity is not None:
            clauses.append("agent_identity = ?")
            params.append(agent_identity)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY granted_at DESC, id"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_grant_row_to_dict(r) for r in rows]

    async def touch_grant(self, grant_id: str, now: float) -> None:
        await self._db.execute(
            "UPDATE broker_grants SET last_used_at = ? WHERE id = ?",
            (now, grant_id),
        )
        await self._db.commit()

    async def sweep_expired(self, now: float) -> int:
        """Flip active grants whose expires_at <= now to status=expired.

        Returns the number of grants flipped.
        """
        cur = await self._db.execute(
            "UPDATE broker_grants SET status = 'expired' "
            "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        await self._db.commit()
        return cur.rowcount

    # --- virtual creds --------------------------------------------------

    async def mint_virtual_cred(self, grant_id: str) -> str:
        token = _TOKEN_PREFIX + _secrets.token_urlsafe(32)
        await self._db.execute(
            "INSERT INTO broker_virtual_creds (token, grant_id, created_at) "
            "VALUES (?, ?, ?)",
            (token, grant_id, time.time()),
        )
        await self._db.commit()
        return token

    async def lookup_virtual_cred(self, token: str) -> dict | None:
        async with self._db.execute(
            "SELECT token, grant_id, created_at FROM broker_virtual_creds "
            "WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return {"token": row[0], "grant_id": row[1], "created_at": row[2]}

    async def delete_virtual_creds_for_grant(self, grant_id: str) -> int:
        cur = await self._db.execute(
            "DELETE FROM broker_virtual_creds WHERE grant_id = ?", (grant_id,)
        )
        await self._db.commit()
        return cur.rowcount


def default_broker_path(data_dir: str | Path) -> Path:
    """Canonical broker db path for a controller data dir."""
    return Path(data_dir) / ".broker.db"
