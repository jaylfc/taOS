"""SQLite-backed scheduler task history.

Write-through cache: the in-memory deque stays the hot-path structure for
recent queries, but every terminal transition (complete / error / rejected)
is also persisted here so history survives restart and the Activity app
can show windows beyond the 500-entry deque cap.

Writes are fire-and-forget via ``asyncio.create_task`` so the scheduler's
submit path stays sync-free, lost writes on a hard crash are acceptable
because the history is an observability aid, not a durable audit log.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

from tinyagentos.db_migrations import apply_wal_pragmas_async
from tinyagentos.scheduler.types import TaskRecord, TaskStatus

logger = logging.getLogger(__name__)


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    capability      TEXT NOT NULL,
    submitter       TEXT,
    priority        INTEGER,
    resource        TEXT,
    status          TEXT NOT NULL,
    submitted_at    REAL NOT NULL,
    started_at      REAL,
    completed_at    REAL,
    elapsed_seconds REAL,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_task_id ON scheduler_history(task_id);
CREATE INDEX IF NOT EXISTS idx_history_submitted_at ON scheduler_history(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_resource ON scheduler_history(resource);
CREATE INDEX IF NOT EXISTS idx_history_capability ON scheduler_history(capability);
"""


class HistoryStore:
    """Persistent scheduler task history.

    Usage:
        store = HistoryStore(Path("data/scheduler_history.db"))
        await store.init()
        # later:
        await store.record_terminal(task_record)
        rows = await store.since(timestamp, limit=500)
        await store.close()
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.path))
        self._db.row_factory = aiosqlite.Row
        await apply_wal_pragmas_async(self._db)
        await self._db.executescript(CREATE_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def record_terminal(self, record: TaskRecord) -> None:
        """Persist a terminal transition (complete / error / rejected).

        Non-terminal states (queued / running) are not persisted, they're
        short-lived and only useful for the in-memory view. Only the final
        outcome makes the DB.
        """
        if self._db is None:
            return
        if record.status not in (
            TaskStatus.COMPLETE,
            TaskStatus.ERROR,
            TaskStatus.REJECTED,
            TaskStatus.CANCELLED,
        ):
            return
        try:
            await self._db.execute(
                """
                INSERT INTO scheduler_history (
                    task_id, capability, submitter, priority, resource, status,
                    submitted_at, started_at, completed_at, elapsed_seconds, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.capability,
                    record.submitter,
                    int(record.priority),
                    record.resource,
                    record.status.value,
                    record.submitted_at,
                    record.started_at,
                    record.completed_at,
                    record.elapsed_seconds,
                    record.error,
                ),
            )
            await self._db.commit()
        except Exception:
            logger.exception("failed to persist scheduler task record")

    async def since(self, timestamp: float, limit: int = 500) -> list[dict]:
        """Return records completed after ``timestamp``, newest first."""
        if self._db is None:
            return []
        cursor = await self._db.execute(
            """
            SELECT * FROM scheduler_history
            WHERE submitted_at >= ?
            ORDER BY submitted_at DESC
            LIMIT ?
            """,
            (timestamp, int(limit)),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def by_resource(self, resource_name: str, limit: int = 100) -> list[dict]:
        if self._db is None:
            return []
        cursor = await self._db.execute(
            """
            SELECT * FROM scheduler_history
            WHERE resource = ?
            ORDER BY submitted_at DESC
            LIMIT ?
            """,
            (resource_name, int(limit)),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def by_capability(self, capability: str, limit: int = 100) -> list[dict]:
        if self._db is None:
            return []
        cursor = await self._db.execute(
            """
            SELECT * FROM scheduler_history
            WHERE capability = ?
            ORDER BY submitted_at DESC
            LIMIT ?
            """,
            (capability, int(limit)),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def stats(self) -> dict:
        """Aggregate counters across all persisted history."""
        if self._db is None:
            return {}
        cursor = await self._db.execute(
            """
            SELECT status, COUNT(*) as n FROM scheduler_history GROUP BY status
            """
        )
        rows = await cursor.fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        cursor = await self._db.execute(
            "SELECT COUNT(*) as n FROM scheduler_history"
        )
        total_row = await cursor.fetchone()
        return {
            "total": total_row["n"] if total_row else 0,
            "by_status": by_status,
        }
