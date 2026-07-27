from __future__ import annotations

import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

STRIKE_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_strikes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step TEXT NOT NULL,
    log_tail TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strikes_task ON task_strikes(task_id);
"""


def _row(r) -> dict:
    keys = ("id", "task_id", "step", "log_tail", "actor", "created_at")
    return dict(zip(keys, r))


class StrikeStore(BaseStore):
    """Append-only log of verification strikes per task.

    The dispatch host (taOS-dev) records a strike each time a card fails
    verification.  When the count reaches ``STRIKE_THRESHOLD`` the task is
    quarantined (see ``ProjectTaskStore.quarantine_task``) and the lead is
    notified.  Strikes are auto-cleared when the task closes or its PR
    merges (see ``ProjectTaskStore.close_task`` and the unquarantine route).
    """

    SCHEMA = STRIKE_SCHEMA

    # Number of failed verifications before a card is quarantined.
    STRIKE_THRESHOLD = 3

    async def record_strike(
        self,
        task_id: str,
        step: str,
        log_tail: str = "",
        actor: str = "",
    ) -> int:
        """Record one verification strike for *task_id*.

        Returns the total strike count for the task after this insert (so the
        caller can decide whether the threshold was just crossed).
        """
        sid = new_id("str")
        now = time.time()
        await self._db.execute(
            """INSERT INTO task_strikes
               (id, task_id, step, log_tail, actor, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sid, task_id, step, log_tail, actor, now),
        )
        await self._db.commit()
        return await self.count_strikes(task_id)

    async def count_strikes(self, task_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM task_strikes WHERE task_id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def list_strikes(self, task_id: str) -> list[dict]:
        """All strikes for *task_id*, oldest first."""
        async with self._db.execute(
            "SELECT * FROM task_strikes WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def latest(self, task_id: str) -> dict | None:
        """The most recent strike for *task_id*, or None."""
        async with self._db.execute(
            "SELECT * FROM task_strikes WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row(row) if row else None

    async def clear_strikes(self, task_id: str) -> int:
        """Delete every strike for *task_id*.

        Returns the number of rows deleted (0 when there were none).
        """
        cursor = await self._db.execute(
            "DELETE FROM task_strikes WHERE task_id = ?", (task_id,)
        )
        await self._db.commit()
        return cursor.rowcount
