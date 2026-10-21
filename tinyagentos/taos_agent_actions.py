"""taOS Agent action gate: pending -> confirmed | declined -> executing -> executed | failed.

Spec (taOS Agent full authority) section 3.1. Every tier-B operation the
agent wants to perform (an external write, an irreversible local change) is
first recorded here as a PENDING action. Only a confirmed action can be
executed, and only once: ``claim_for_execution`` flips ``confirmed`` to
``executing`` in a single conditional UPDATE, so two concurrent execute calls
cannot both win. The row becomes ``executed`` only after the executor returns
(``mark_executed``) or ``failed`` when it raises. A row left ``executing`` (the
process stopped mid-write) is never replayed: the external write may already
have happened, so the user sees it in the action list and decides.

Executors are plain async callables registered per kind on
``app.state.taos_agent_action_executors`` (``{kind: async fn(action) -> dict}``);
the gate never runs a payload itself.
"""
from __future__ import annotations

import json
import time
import uuid

from tinyagentos.base_store import BaseStore

STATE_PENDING = "pending"
STATE_CONFIRMED = "confirmed"
STATE_DECLINED = "declined"
STATE_EXECUTING = "executing"
STATE_EXECUTED = "executed"
STATE_FAILED = "failed"

CONFIRMED_BY_USER = "user"
CONFIRMED_BY_AUTO = "auto"
CONFIRMED_BY_DECLINED = "declined"

POLICY_ASK = "ask"
POLICY_AUTO = "auto"
POLICIES = frozenset({POLICY_ASK, POLICY_AUTO})


class TaosAgentActionStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS taos_agent_actions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        summary TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL,
        confirmed_by TEXT,
        error TEXT,
        created_at REAL NOT NULL,
        decided_at REAL,
        executed_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_taos_agent_actions_user
        ON taos_agent_actions(user_id, created_at);
    """

    @staticmethod
    def _row(cursor, row) -> dict:
        out = {d[0]: row[i] for i, d in enumerate(cursor.description)}
        out["payload"] = json.loads(out["payload"] or "{}")
        return out

    async def get(self, action_id: str) -> dict | None:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM taos_agent_actions WHERE id = ?", (action_id,)
        )
        row = await cursor.fetchone()
        return self._row(cursor, row) if row else None

    async def create(
        self, *, user_id: str, kind: str, summary: str, payload: dict, auto: bool
    ) -> dict:
        """Record a new action. ``auto`` stores it already confirmed by policy."""
        assert self._db is not None
        now = time.time()
        action_id = uuid.uuid4().hex
        state = STATE_CONFIRMED if auto else STATE_PENDING
        await self._db.execute(
            "INSERT INTO taos_agent_actions "
            "(id, user_id, kind, summary, payload, state, confirmed_by, created_at, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id, user_id, kind, summary, json.dumps(payload), state,
                CONFIRMED_BY_AUTO if auto else None, now, now if auto else None,
            ),
        )
        await self._db.commit()
        return await self.get(action_id)

    async def decide(self, action_id: str, *, confirm: bool) -> dict | None:
        """Move a PENDING action to confirmed or declined.

        Returns the updated row, or None when the action was not pending
        (already decided, executed, or missing).
        """
        assert self._db is not None
        state = STATE_CONFIRMED if confirm else STATE_DECLINED
        by = CONFIRMED_BY_USER if confirm else CONFIRMED_BY_DECLINED
        cursor = await self._db.execute(
            "UPDATE taos_agent_actions SET state = ?, confirmed_by = ?, decided_at = ? "
            "WHERE id = ? AND state = ?",
            (state, by, time.time(), action_id, STATE_PENDING),
        )
        await self._db.commit()
        if cursor.rowcount != 1:
            return None
        return await self.get(action_id)

    async def list_for_user(self, user_id: str, *, state: str | None = None) -> list[dict]:
        """The owner's actions, oldest first, optionally filtered by state."""
        assert self._db is not None
        sql = "SELECT * FROM taos_agent_actions WHERE user_id = ?"
        args: tuple = (user_id,)
        if state is not None:
            sql += " AND state = ?"
            args += (state,)
        cursor = await self._db.execute(sql + " ORDER BY created_at LIMIT 200", args)
        return [self._row(cursor, row) for row in await cursor.fetchall()]

    async def claim_for_execution(self, action_id: str) -> dict | None:
        """Atomically flip confirmed -> executing. None unless this call won."""
        assert self._db is not None
        cursor = await self._db.execute(
            "UPDATE taos_agent_actions SET state = ? WHERE id = ? AND state = ?",
            (STATE_EXECUTING, action_id, STATE_CONFIRMED),
        )
        await self._db.commit()
        if cursor.rowcount != 1:
            return None
        return await self.get(action_id)

    async def mark_executed(self, action_id: str) -> dict | None:
        """Record that the executor finished: executing -> executed."""
        assert self._db is not None
        await self._db.execute(
            "UPDATE taos_agent_actions SET state = ?, executed_at = ? WHERE id = ? AND state = ?",
            (STATE_EXECUTED, time.time(), action_id, STATE_EXECUTING),
        )
        await self._db.commit()
        return await self.get(action_id)

    async def mark_failed(self, action_id: str, error: str) -> None:
        """Record an executor failure. A failed action is never re-executable."""
        assert self._db is not None
        await self._db.execute(
            "UPDATE taos_agent_actions SET state = ?, error = ? WHERE id = ?",
            (STATE_FAILED, error[:500], action_id),
        )
        await self._db.commit()
