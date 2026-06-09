from __future__ import annotations

"""Store for per-agent scope grants minted by the consent loop.

A grant records that an admin approved a specific scope for an external agent.
Phase 1 only writes tier='once' (single-decision, no expiry); the full
permission-tier system (time-boxed, project-scoped, always-allow, always-block)
is Phase 2, built on top of this table.

The Permissions app and the A2A bus read this table to check what an agent
may do.  ``list_active_grants`` is the feed @taOSmd polls later.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_grants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT    NOT NULL,
    scope        TEXT    NOT NULL,
    tier         TEXT    NOT NULL DEFAULT 'once',
    project_id   TEXT,
    granted_at   TEXT    NOT NULL,
    expires_at   TEXT,
    UNIQUE (canonical_id, scope)
);
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class AgentGrantsStore(BaseStore):
    """Persistent store for per-agent scope grants."""

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def add_grant(
        self,
        canonical_id: str,
        scope: str,
        *,
        tier: str = "once",
        project_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> dict:
        """Insert or replace a grant for (canonical_id, scope).

        Uses INSERT OR REPLACE so re-approving a scope is idempotent.
        """
        if self._db is None:
            raise RuntimeError("AgentGrantsStore not initialised — call init() first")

        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO agent_grants
                (canonical_id, scope, tier, project_id, granted_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (canonical_id, scope, tier, project_id, now, expires_at),
        )
        await self._db.commit()
        row = await (
            await self._db.execute(
                "SELECT * FROM agent_grants WHERE canonical_id = ? AND scope = ?",
                (canonical_id, scope),
            )
        ).fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_grants(self, canonical_id: str) -> list[dict]:
        """Return all grants for *canonical_id*."""
        if self._db is None:
            raise RuntimeError("AgentGrantsStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM agent_grants WHERE canonical_id = ? ORDER BY granted_at",
            (canonical_id,),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]

    async def list_active_grants(self) -> list[dict]:
        """Return all grants that are not yet expired.

        Phase 1: no expiry is set (expires_at IS NULL), so every row is active.
        Phase 2: add WHERE (expires_at IS NULL OR expires_at > now).
        """
        if self._db is None:
            raise RuntimeError("AgentGrantsStore not initialised")
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "SELECT * FROM agent_grants "
            "WHERE expires_at IS NULL OR expires_at > ? "
            "ORDER BY canonical_id, scope",
            (now,),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]
