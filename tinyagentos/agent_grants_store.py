from __future__ import annotations

"""Store for per-agent scope grants minted by the consent loop.

A grant records that an admin approved a specific scope for an external agent.
Phase 1 only writes tier='once' (single-decision, no expiry); the full
permission-tier system (time-boxed, project-scoped, always-allow, always-block)
is Phase 2, built on top of this table.

The Permissions app and the A2A bus read this table to check what an agent
may do.  ``list_active_grants`` is the feed @taOSmd polls later.
"""

import asyncio
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
    UNIQUE (canonical_id, scope, project_id)
);
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class AgentGrantsStore(BaseStore):
    """Persistent store for per-agent scope grants."""

    SCHEMA = SCHEMA

    # Serializes the DELETE-then-INSERT-then-SELECT in add_grant. The 3-column
    # UNIQUE with a nullable project_id forces a non-atomic delete+insert
    # (INSERT OR REPLACE cannot dedup a NULL project_id), so two concurrent
    # same-key writes could interleave (one DELETE removes the other's row
    # before its SELECT-back, or the second INSERT hits the unique). The lock
    # makes each write atomic against the others on this single connection.
    _write_lock: asyncio.Lock

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row
        self._write_lock = asyncio.Lock()

    async def _post_init(self) -> None:
        """Upgrade an EXISTING database whose agent_grants table still has the
        old 2-column unique ``(canonical_id, scope)`` to the per-project
        3-column unique ``(canonical_id, scope, project_id)``.

        A table-level UNIQUE constraint cannot be ALTERed, so we do the standard
        SQLite rebuild dance (create new table, copy rows, drop old, rename).
        The copy is safe: the OLD unique guaranteed at most one row per
        (canonical_id, scope), so no duplicate-key conflict can arise under the
        new 3-column unique. Guarded so it only runs when the legacy constraint
        is present, making it idempotent on already-migrated DBs. This is a
        boot-path migration and must never crash an existing DB's init().
        """
        if self._db is None:  # pragma: no cover - defensive
            return

        # Detect the legacy 2-column unique by inspecting the table's SQL.
        # row_factory may not be set yet (init() sets it after super().init()),
        # so read the column positionally.
        cur = await self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_grants'"
        )
        row = await cur.fetchone()
        sql = row[0] if row else ""
        # Legacy DDL ends the table constraint with UNIQUE (canonical_id, scope)
        # and lacks the project_id column in the unique list. The fresh SCHEMA
        # contains "UNIQUE (canonical_id, scope, project_id)".
        if "UNIQUE (canonical_id, scope, project_id)" in sql:
            return  # already migrated

        async with self._db.cursor() as cur2:
            await cur2.execute("SELECT name FROM pragma_table_info('agent_grants')")
            cols = {r[0] for r in await cur2.fetchall()}
        if "project_id" not in cols:  # pragma: no cover - defensive
            return  # current schema already applied by SCHEMA; nothing to do

        await self._db.execute("BEGIN")
        try:
            await self._db.execute(
                """
                CREATE TABLE agent_grants_new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_id TEXT    NOT NULL,
                    scope        TEXT    NOT NULL,
                    tier         TEXT    NOT NULL DEFAULT 'once',
                    project_id   TEXT,
                    granted_at   TEXT    NOT NULL,
                    expires_at   TEXT,
                    UNIQUE (canonical_id, scope, project_id)
                );
                """
            )
            await self._db.execute(
                """
                INSERT INTO agent_grants_new
                    (id, canonical_id, scope, tier, project_id, granted_at, expires_at)
                SELECT id, canonical_id, scope, tier, project_id, granted_at, expires_at
                FROM agent_grants;
                """
            )
            await self._db.execute("DROP TABLE agent_grants")
            await self._db.execute(
                "ALTER TABLE agent_grants_new RENAME TO agent_grants"
            )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

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
        """Insert or replace a grant for (canonical_id, scope, project_id).

        The 3-column UNIQUE key treats NULL project_id as distinct, so a bare
        ``INSERT OR REPLACE`` would NOT replace an existing NULL-project_id row
        (it would append a second one). We make re-approval idempotent by
        explicitly deleting the matching key (NULL-safe via IS) before inserting.
        """
        if self._db is None:
            raise RuntimeError("AgentGrantsStore not initialised — call init() first")

        now = datetime.now(timezone.utc).isoformat()
        # Serialize the delete+insert+select so two concurrent same-key writes
        # cannot interleave (which would either drop each other's row before the
        # SELECT-back returns None, or collide on the UNIQUE at INSERT).
        async with self._write_lock:
            # Remove any existing row for the exact key first (NULL-safe match).
            await self._db.execute(
                "DELETE FROM agent_grants "
                "WHERE canonical_id = ? AND scope = ? AND project_id IS ?",
                (canonical_id, scope, project_id),
            )
            await self._db.execute(
                """
                INSERT INTO agent_grants
                    (canonical_id, scope, tier, project_id, granted_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (canonical_id, scope, tier, project_id, now, expires_at),
            )
            await self._db.commit()
            row = await (
                await self._db.execute(
                    # Select by the full 3-column key. project_id is nullable, so
                    # match NULL with IS (a plain ``=`` would never match NULL).
                    "SELECT * FROM agent_grants "
                    "WHERE canonical_id = ? AND scope = ? AND project_id IS ?",
                    (canonical_id, scope, project_id),
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
