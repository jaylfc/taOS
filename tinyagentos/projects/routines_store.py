from __future__ import annotations

import secrets
import time

from croniter import croniter

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

ROUTINES_SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body_template TEXT NOT NULL DEFAULT '',
    assignee_id TEXT,
    trigger_kind TEXT NOT NULL DEFAULT 'cron',
    cron_expr TEXT,
    webhook_token TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_fired REAL,
    next_fire REAL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routines_project ON routines(project_id);
CREATE INDEX IF NOT EXISTS idx_routines_due ON routines(enabled, next_fire);
"""


def _row_to_routine(row, description) -> dict:
    keys = [d[0] for d in description]
    return dict(zip(keys, row))


def _compute_next_fire(cron_expr: str, base_ts: float) -> float:
    return croniter(cron_expr, base_ts).get_next(float)


class RoutineStore(BaseStore):
    SCHEMA = ROUTINES_SCHEMA

    async def create_routine(
        self,
        project_id: str,
        title: str,
        created_by: str,
        body_template: str = "",
        assignee_id: str | None = None,
        trigger_kind: str = "cron",
        cron_expr: str | None = None,
        enabled: bool = True,
    ) -> dict:
        if trigger_kind not in ("cron", "webhook", "api"):
            raise ValueError(f"invalid trigger_kind: {trigger_kind}")
        if trigger_kind == "cron" and not cron_expr:
            raise ValueError("cron_expr is required for trigger_kind='cron'")

        rid = new_id("rtn")
        now = time.time()
        webhook_token = secrets.token_urlsafe(32) if trigger_kind == "webhook" else None
        next_fire = _compute_next_fire(cron_expr, now) if trigger_kind == "cron" and enabled else None

        await self._db.execute(
            """INSERT INTO routines
               (id, project_id, title, body_template, assignee_id, trigger_kind,
                cron_expr, webhook_token, enabled, last_fired, next_fire,
                created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
            (
                rid, project_id, title, body_template, assignee_id, trigger_kind,
                cron_expr, webhook_token, int(enabled), next_fire, created_by, now, now,
            ),
        )
        await self._db.commit()
        return await self.get_routine(rid)

    async def get_routine(self, routine_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM routines WHERE id = ?", (routine_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_routine(row, cur.description)

    async def get_by_webhook_token(self, token: str) -> dict | None:
        """Look up an enabled webhook routine by its token.

        Returns None for any non-match (unknown token, disabled routine, or
        wrong trigger_kind) so callers can 404 uniformly without leaking which
        case applied.
        """
        async with self._db.execute(
            "SELECT * FROM routines WHERE trigger_kind = 'webhook' AND enabled = 1"
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        for row in rows:
            routine = _row_to_routine(row, desc)
            if secrets.compare_digest(routine.get("webhook_token") or "", token):
                return routine
        return None

    async def list_routines(self, project_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM routines WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_routine(r, desc) for r in rows]

    async def list_due(self, now_ts: float) -> list[dict]:
        async with self._db.execute(
            """SELECT * FROM routines
               WHERE trigger_kind = 'cron' AND enabled = 1
                 AND next_fire IS NOT NULL AND next_fire <= ?
               ORDER BY next_fire ASC""",
            (now_ts,),
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_routine(r, desc) for r in rows]

    async def update_routine(
        self,
        routine_id: str,
        title: str | None = None,
        body_template: str | None = None,
        assignee_id: str | None = None,
        cron_expr: str | None = None,
        enabled: bool | None = None,
    ) -> dict | None:
        existing = await self.get_routine(routine_id)
        if existing is None:
            return None

        candidates = [
            ("title", title),
            ("body_template", body_template),
            ("assignee_id", assignee_id),
            ("cron_expr", cron_expr),
        ]
        sets: list[str] = []
        params: list = []
        for col, val in candidates:
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(int(enabled))

        new_cron_expr = cron_expr if cron_expr is not None else existing["cron_expr"]
        new_enabled = enabled if enabled is not None else bool(existing["enabled"])
        # Recompute next_fire whenever the schedule or enabled flag could have
        # changed what "due" means for this routine.
        if existing["trigger_kind"] == "cron" and (cron_expr is not None or enabled is not None):
            next_fire = _compute_next_fire(new_cron_expr, time.time()) if new_enabled and new_cron_expr else None
            sets.append("next_fire = ?")
            params.append(next_fire)

        if not sets:
            return existing

        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(routine_id)
        await self._db.execute(
            f"UPDATE routines SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_routine(routine_id)

    async def record_fire(self, routine_id: str, now_ts: float) -> dict | None:
        """Stamp last_fired and advance next_fire (cron routines only)."""
        existing = await self.get_routine(routine_id)
        if existing is None:
            return None
        next_fire = None
        if existing["trigger_kind"] == "cron" and existing["cron_expr"] and existing["enabled"]:
            next_fire = _compute_next_fire(existing["cron_expr"], now_ts)
        await self._db.execute(
            "UPDATE routines SET last_fired = ?, next_fire = ?, updated_at = ? WHERE id = ?",
            (now_ts, next_fire, now_ts, routine_id),
        )
        await self._db.commit()
        return await self.get_routine(routine_id)

    async def delete_routine(self, routine_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        await self._db.commit()
        return cursor.rowcount > 0
