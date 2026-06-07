"""Multi-Agent Memory Leases (taOSmd).

TTL-based exclusive locks for memory operations, preventing race conditions
when multiple agents access shared memory stores. Essential for taOS where
agents run in separate containers but share the controller's memory backend.

Lease model:
  - Agent acquires a lease on a resource key (e.g., "kg:jay", "vector:project-x")
  - Lease has a TTL (default 10 min, max 1 hour)
  - Only the lease holder can write to that resource
  - Expired leases are auto-cleaned on any operation
  - Agents can renew or release leases explicitly
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from pathlib import Path

from tinyagentos.db_migrations import apply_wal_pragmas, run_migrations

SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    resource_key TEXT NOT NULL UNIQUE,
    agent_name TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    renewed_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_leases_resource ON leases(resource_key);
CREATE INDEX IF NOT EXISTS idx_leases_agent ON leases(agent_name);
CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases(expires_at);
"""

MIGRATIONS: list = [
    (1, SCHEMA),
]

DEFAULT_TTL = 600      # 10 minutes
MAX_TTL = 3600         # 1 hour
MAX_RENEW_COUNT = 5    # Prevent infinite lease hogging


class LeaseManager:
    """SQLite-backed lease manager for multi-agent coordination.

    All public methods are async.  Blocking sqlite3 calls are dispatched to a
    thread via asyncio.to_thread so they never stall the event loop.
    """

    def __init__(self, db_path: str | Path = "data/leases.db"):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def _sync_init(self) -> None:
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        apply_wal_pragmas(self._conn)
        run_migrations(self._conn, MIGRATIONS)

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._sync_init)

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _cleanup_expired(self) -> int:
        """Remove expired leases. Returns count removed."""
        now = time.time()
        cursor = self._conn.execute(
            "DELETE FROM leases WHERE expires_at < ?", (now,)
        )
        if cursor.rowcount > 0:
            self._conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Acquire / renew / release  (sync helpers)
    # ------------------------------------------------------------------

    def _sync_acquire(
        self,
        resource_key: str,
        agent_name: str,
        ttl: float,
    ) -> dict | None:
        ttl = min(ttl, MAX_TTL)
        self._cleanup_expired()
        now = time.time()

        existing = self._conn.execute(
            "SELECT * FROM leases WHERE resource_key = ?",
            (resource_key,),
        ).fetchone()

        if existing:
            if existing["agent_name"] == agent_name:
                # Same agent — auto-renew
                return self._sync_renew(resource_key, agent_name, ttl)
            return None

        lease_id = uuid.uuid4().hex[:16]
        self._conn.execute(
            """INSERT INTO leases (id, resource_key, agent_name, acquired_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (lease_id, resource_key, agent_name, now, now + ttl),
        )
        self._conn.commit()

        return {
            "id": lease_id,
            "resource_key": resource_key,
            "agent_name": agent_name,
            "acquired_at": now,
            "expires_at": now + ttl,
            "renewed_count": 0,
        }

    def _sync_renew(
        self,
        resource_key: str,
        agent_name: str,
        ttl: float,
    ) -> dict | None:
        ttl = min(ttl, MAX_TTL)
        now = time.time()

        existing = self._conn.execute(
            "SELECT * FROM leases WHERE resource_key = ? AND agent_name = ?",
            (resource_key, agent_name),
        ).fetchone()

        if not existing:
            return None

        if existing["renewed_count"] >= MAX_RENEW_COUNT:
            return None

        self._conn.execute(
            "UPDATE leases SET expires_at = ?, renewed_count = renewed_count + 1 WHERE id = ?",
            (now + ttl, existing["id"]),
        )
        self._conn.commit()

        return {
            "id": existing["id"],
            "resource_key": resource_key,
            "agent_name": agent_name,
            "acquired_at": existing["acquired_at"],
            "expires_at": now + ttl,
            "renewed_count": existing["renewed_count"] + 1,
        }

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def acquire(
        self,
        resource_key: str,
        agent_name: str,
        ttl: float = DEFAULT_TTL,
    ) -> dict | None:
        """Acquire an exclusive lease on a resource.

        Returns lease dict on success, None if resource is already held
        by another agent.
        """
        return await asyncio.to_thread(self._sync_acquire, resource_key, agent_name, ttl)

    async def renew(
        self,
        resource_key: str,
        agent_name: str,
        ttl: float = DEFAULT_TTL,
    ) -> dict | None:
        """Renew an existing lease. Only the holder can renew."""
        return await asyncio.to_thread(self._sync_renew, resource_key, agent_name, ttl)

    async def release(self, resource_key: str, agent_name: str) -> bool:
        """Release a lease. Only the holder can release."""
        def _do() -> bool:
            cursor = self._conn.execute(
                "DELETE FROM leases WHERE resource_key = ? AND agent_name = ?",
                (resource_key, agent_name),
            )
            self._conn.commit()
            return cursor.rowcount > 0
        return await asyncio.to_thread(_do)

    async def check(self, resource_key: str) -> dict | None:
        """Check who holds a lease on a resource. Returns lease dict or None."""
        def _do() -> dict | None:
            self._cleanup_expired()
            row = self._conn.execute(
                "SELECT * FROM leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
            return dict(row) if row else None
        return await asyncio.to_thread(_do)

    async def is_held_by(self, resource_key: str, agent_name: str) -> bool:
        """Check if a specific agent holds the lease."""
        def _do() -> bool:
            self._cleanup_expired()
            row = self._conn.execute(
                "SELECT 1 FROM leases WHERE resource_key = ? AND agent_name = ?",
                (resource_key, agent_name),
            ).fetchone()
            return row is not None
        return await asyncio.to_thread(_do)

    async def agent_leases(self, agent_name: str) -> list[dict]:
        """List all active leases held by an agent."""
        def _do() -> list[dict]:
            self._cleanup_expired()
            rows = self._conn.execute(
                "SELECT * FROM leases WHERE agent_name = ? ORDER BY acquired_at",
                (agent_name,),
            ).fetchall()
            return [dict(r) for r in rows]
        return await asyncio.to_thread(_do)

    async def release_all(self, agent_name: str) -> int:
        """Release all leases held by an agent (e.g., on agent shutdown)."""
        def _do() -> int:
            cursor = self._conn.execute(
                "DELETE FROM leases WHERE agent_name = ?",
                (agent_name,),
            )
            self._conn.commit()
            return cursor.rowcount
        return await asyncio.to_thread(_do)

    async def stats(self) -> dict:
        """Lease statistics."""
        def _do() -> dict:
            self._cleanup_expired()
            total = self._conn.execute(
                "SELECT COUNT(*) as n FROM leases"
            ).fetchone()["n"]
            agents = self._conn.execute(
                "SELECT COUNT(DISTINCT agent_name) as n FROM leases"
            ).fetchone()["n"]
            return {"active_leases": total, "agents_with_leases": agents}
        return await asyncio.to_thread(_do)
