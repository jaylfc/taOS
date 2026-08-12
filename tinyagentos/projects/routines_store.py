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
-- Webhook tokens are high-entropy opaque strings; a unique index makes the
-- token->routine lookup an indexed O(log n) probe. SQLite treats NULLs as
-- distinct, so the many non-webhook rows (token IS NULL) don't collide.
CREATE UNIQUE INDEX IF NOT EXISTS idx_routines_webhook_token ON routines(webhook_token);
"""


def _row_to_routine(row, description) -> dict:
    keys = [d[0] for d in description]
    return dict(zip(keys, row))


def _validate_cron(cron_expr: str) -> None:
    """Raise ValueError if cron_expr is not a valid cron expression.

    Without this, an invalid schedule reaches croniter only when next_fire is
    computed and raises an uncaught error (croniter's own CroniterBadCronError /
    ValueError) — surfacing as a 500. Validating up front lets the route map it
    to a clean 400.
    """
    if not croniter.is_valid(cron_expr):
        raise ValueError(f"invalid cron expression: {cron_expr!r}")


def _compute_next_fire(cron_expr: str, base_ts: float) -> float:
    return croniter(cron_expr, base_ts).get_next(float)


class RoutineStore(BaseStore):
    SCHEMA = ROUTINES_SCHEMA
    _clock = staticmethod(time.time)

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
        if trigger_kind == "cron":
            if not cron_expr:
                raise ValueError("cron_expr is required for trigger_kind='cron'")
            _validate_cron(cron_expr)

        rid = new_id("rtn")
        now = self._clock()
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
        """Look up an enabled webhook routine by its token via the unique index.

        The token is a high-entropy opaque secret, so an exact indexed lookup is
        the correct (and fastest) match — no per-row comparison needed. Returns
        None for any non-match (unknown token, disabled routine, or wrong
        trigger_kind, including an empty token) so callers can 404 uniformly
        without leaking which case applied.
        """
        if not token:
            return None
        async with self._db.execute(
            """SELECT * FROM routines
               WHERE webhook_token = ? AND trigger_kind = 'webhook' AND enabled = 1""",
            (token,),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_routine(row, cur.description)

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

        if cron_expr is not None:
            _validate_cron(cron_expr)

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
            next_fire = _compute_next_fire(new_cron_expr, self._clock()) if new_enabled and new_cron_expr else None
            sets.append("next_fire = ?")
            params.append(next_fire)

        if not sets:
            return existing

        sets.append("updated_at = ?")
        params.append(self._clock())
        params.append(routine_id)
        await self._db.execute(
            f"UPDATE routines SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_routine(routine_id)

    async def record_fire(self, routine_id: str, now_ts: float) -> dict | None:
        """Stamp last_fired and advance next_fire (cron routines only).

        Used by the manual/API and webhook trigger paths, where there is no
        double-fire race (a human/caller drives them). The scheduler's tick
        loop must instead use claim_due(), which advances the schedule
        atomically under a guard so a concurrent/restarted tick can't re-fire
        the same due routine.
        """
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

    async def claim_due(
        self, routine_id: str, expected_next_fire: float, now_ts: float
    ) -> bool:
        """Atomically claim a due cron routine for firing.

        Advances next_fire (and stamps last_fired) in a single UPDATE guarded on
        the routine still having the next_fire the caller selected it with. If a
        concurrent or restarted tick already claimed it, next_fire no longer
        matches and rowcount is 0 — so the routine fires exactly once per due
        instant. Returns True only for the tick that won the claim.
        """
        existing = await self.get_routine(routine_id)
        if (
            existing is None
            or existing["trigger_kind"] != "cron"
            or not existing["enabled"]
            or not existing["cron_expr"]
        ):
            return False
        next_fire = _compute_next_fire(existing["cron_expr"], now_ts)
        cursor = await self._db.execute(
            """UPDATE routines
               SET last_fired = ?, next_fire = ?, updated_at = ?
               WHERE id = ? AND enabled = 1 AND next_fire = ?""",
            (now_ts, next_fire, now_ts, routine_id, expected_next_fire),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def delete_routine(self, routine_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        await self._db.commit()
        return cursor.rowcount > 0
