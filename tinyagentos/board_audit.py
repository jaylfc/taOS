from __future__ import annotations

import datetime
import json
import secrets

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

BOARD_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS board_audit (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    event TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    from_status TEXT,
    to_status TEXT,
    detail TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_board_audit_task ON board_audit(task_id);
CREATE INDEX IF NOT EXISTS idx_board_audit_ts ON board_audit(ts);
"""
# The project_id index is created in _post_init (not SCHEMA): on an existing
# board_audit table that predates the column, running it here would fail before
# the guarded ALTER had a chance to add the column.

_COLS = "id, task_id, project_id, event, actor, from_status, to_status, detail, ts"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _new_id() -> str:
    return "ba-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _row(r) -> dict:
    return {
        "id": r[0], "task_id": r[1], "project_id": r[2], "event": r[3], "actor": r[4],
        "from_status": r[5], "to_status": r[6], "detail": json.loads(r[7] or "{}"), "ts": r[8],
    }


class BoardAuditLog(BaseStore):
    """Append-only audit trail of board task state changes (#105).

    The seed of the nothing-is-ever-deleted / Time Machine story: every status
    change is recorded and never updated or deleted. There is deliberately no
    public mutate or delete method. History is returned in insertion order
    (SQLite rowid) so it is stable even when two events share a timestamp.

    Each row carries the owning project_id (so a project-scoped activity feed
    never leaks another project's events) and a free-form JSON ``detail`` blob
    for event-specific context that does not fit the status columns.
    """

    SCHEMA = BOARD_AUDIT_SCHEMA

    async def _post_init(self) -> None:
        # project_id + detail were added after the initial board_audit ship.
        # Guarded ALTER so existing databases gain them without a destructive
        # migration (SQLite lacks ADD COLUMN IF NOT EXISTS before 3.37).
        cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(board_audit)")).fetchall()}
        if "project_id" not in cols:
            await self._db.execute(
                "ALTER TABLE board_audit ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
            )
        if "detail" not in cols:
            await self._db.execute(
                "ALTER TABLE board_audit ADD COLUMN detail TEXT NOT NULL DEFAULT '{}'"
            )
        # The column is guaranteed present now (fresh via SCHEMA or just ALTERed),
        # so the project_id index can be created idempotently for both paths.
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_board_audit_project ON board_audit(project_id)"
        )
        await self._db.commit()

    async def record(
        self,
        task_id: str,
        event: str,
        actor: str = "",
        from_status: str | None = None,
        to_status: str | None = None,
        ts: str | None = None,
        project_id: str = "",
        detail: dict | None = None,
    ) -> str:
        if not task_id:
            raise ValueError("task_id is required")
        if not event:
            raise ValueError("event is required")
        eid = _new_id()
        when = ts or _now_iso()
        await self._db.execute(
            f"INSERT INTO board_audit ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, task_id, project_id, event, actor, from_status, to_status,
             json.dumps(detail or {}), when),
        )
        await self._db.commit()
        return eid

    async def history(self, task_id: str) -> list[dict]:
        async with self._db.execute(
            f"SELECT {_COLS} FROM board_audit WHERE task_id = ? ORDER BY rowid ASC",
            (task_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def recent_for_project(self, project_id: str, limit: int = 100) -> list[dict]:
        """Newest-first activity feed for one project (capped)."""
        async with self._db.execute(
            f"SELECT {_COLS} FROM board_audit WHERE project_id = ? ORDER BY rowid DESC LIMIT ?",
            (project_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def all_since(self, ts: str) -> list[dict]:
        async with self._db.execute(
            f"SELECT {_COLS} FROM board_audit WHERE ts >= ? ORDER BY rowid ASC", (ts,)
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def get(self, event_id: str) -> dict | None:
        async with self._db.execute(
            f"SELECT {_COLS} FROM board_audit WHERE id = ?", (event_id,)
        ) as cur:
            r = await cur.fetchone()
        return _row(r) if r else None
