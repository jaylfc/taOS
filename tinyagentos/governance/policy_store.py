"""Agent governance: execution policies + approval-grant store (Slice 1).

Decides whether a deployed agent's tool call is allowed, denied, or needs a
human approval. Two tables:

- ``execution_policies``: the policy itself, per (scope, agent_name,
  action_class). ``scope`` is ``global`` (agent_name NULL) or ``agent``
  (agent_name set); a per-agent row overrides the global row for the same
  action class.
- ``execution_grants``: a short-lived grant written when a human approves a
  pending "require_approval" gate, so the agent's retry of that exact action
  passes without asking again until the grant expires.

Default posture is CONSERVATIVE: a fixed set of sensitive action classes
require approval out of the box (seeded as global rows in ``_post_init``);
everything else is allowed by the absence of a policy row.
"""

from __future__ import annotations

import secrets
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.governance.action_classes import SENSITIVE_ACTION_CLASSES

EFFECTS = ("allow", "deny", "require_approval")
SCOPES = ("global", "agent")

# Live grant TTL: how long an approved action stays authorized for retry
# before the agent must ask again.
GRANT_TTL_S = 15 * 60

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

EXECUTION_POLICIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_policies (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL,
    agent_name  TEXT,
    action_class TEXT NOT NULL,
    effect      TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_policies_lookup
    ON execution_policies(scope, agent_name, action_class);

CREATE TABLE IF NOT EXISTS execution_grants (
    id           TEXT PRIMARY KEY,
    agent_name   TEXT NOT NULL,
    action_class TEXT NOT NULL,
    granted_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    decision_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_execution_grants_lookup
    ON execution_grants(agent_name, action_class, expires_at DESC);
"""


def _new_id(prefix: str) -> str:
    return f"{prefix}-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _row_to_policy(row) -> dict:
    return {
        "id": row[0],
        "scope": row[1],
        "agent_name": row[2],
        "action_class": row[3],
        "effect": row[4],
        "updated_at": row[5],
    }


class ExecutionPolicyStore(BaseStore):
    SCHEMA = EXECUTION_POLICIES_SCHEMA

    async def _post_init(self) -> None:
        # Seed the CONSERVATIVE default posture once: global require_approval
        # for the sensitive action classes. Only seed if the table is empty so
        # re-running init on an existing database never clobbers policies the
        # user has since edited.
        async with self._db.execute("SELECT COUNT(*) FROM execution_policies") as cur:
            (count,) = await cur.fetchone()
        if count:
            return
        now = time.time()
        for action_class in sorted(SENSITIVE_ACTION_CLASSES):
            await self._db.execute(
                """INSERT INTO execution_policies
                   (id, scope, agent_name, action_class, effect, updated_at)
                   VALUES (?, 'global', NULL, ?, 'require_approval', ?)""",
                (_new_id("pol"), action_class, now),
            )
        await self._db.commit()

    async def set_policy(
        self,
        action_class: str,
        effect: str,
        *,
        agent_name: str | None = None,
    ) -> dict:
        """Create or replace the policy for (scope, agent_name, action_class).
        scope is derived from agent_name: None -> global, else per-agent."""
        if effect not in EFFECTS:
            raise ValueError(f"invalid effect: {effect!r}")
        scope = "agent" if agent_name else "global"
        now = time.time()
        async with self._db.execute(
            "SELECT id FROM execution_policies WHERE scope = ? AND agent_name IS ? AND action_class = ?",
            (scope, agent_name, action_class),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            pid = existing[0]
            await self._db.execute(
                "UPDATE execution_policies SET effect = ?, updated_at = ? WHERE id = ?",
                (effect, now, pid),
            )
        else:
            pid = _new_id("pol")
            await self._db.execute(
                """INSERT INTO execution_policies
                   (id, scope, agent_name, action_class, effect, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (pid, scope, agent_name, action_class, effect, now),
            )
        await self._db.commit()
        async with self._db.execute(
            "SELECT id, scope, agent_name, action_class, effect, updated_at "
            "FROM execution_policies WHERE id = ?",
            (pid,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_policy(row)

    async def list_policies(self, *, agent_name: str | None = None) -> list[dict]:
        """List policies. With no filter, returns every row (global + all
        per-agent overrides); pass agent_name to scope to that agent's
        overrides plus the global rows."""
        if agent_name is None:
            async with self._db.execute(
                "SELECT id, scope, agent_name, action_class, effect, updated_at "
                "FROM execution_policies ORDER BY scope, action_class"
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._db.execute(
                "SELECT id, scope, agent_name, action_class, effect, updated_at "
                "FROM execution_policies WHERE agent_name IS NULL OR agent_name = ? "
                "ORDER BY scope, action_class",
                (agent_name,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_policy(r) for r in rows]

    async def delete_policy(self, policy_id: str) -> bool:
        cur = await self._db.execute(
            "DELETE FROM execution_policies WHERE id = ?", (policy_id,)
        )
        await self._db.commit()
        return cur.rowcount == 1

    async def effective_effect(self, agent_name: str, action_class: str) -> str:
        """Resolve the effect for (agent_name, action_class): a per-agent
        policy wins over the global one; absent either, fall back to the
        hardcoded conservative default (require_approval for the sensitive
        action classes, allow otherwise)."""
        async with self._db.execute(
            "SELECT effect FROM execution_policies WHERE scope = 'agent' "
            "AND agent_name = ? AND action_class = ?",
            (agent_name, action_class),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row[0]
        async with self._db.execute(
            "SELECT effect FROM execution_policies WHERE scope = 'global' "
            "AND action_class = ?",
            (action_class,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row[0]
        return "require_approval" if action_class in SENSITIVE_ACTION_CLASSES else "allow"

    async def add_grant(
        self,
        agent_name: str,
        action_class: str,
        decision_id: str | None = None,
        ttl_s: float = GRANT_TTL_S,
    ) -> dict:
        """Write a short-lived grant so the agent's next attempt at this
        action class passes the require_approval gate without re-asking."""
        now = time.time()
        gid = _new_id("grt")
        await self._db.execute(
            """INSERT INTO execution_grants
               (id, agent_name, action_class, granted_at, expires_at, decision_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (gid, agent_name, action_class, now, now + ttl_s, decision_id),
        )
        await self._db.commit()
        return {
            "id": gid,
            "agent_name": agent_name,
            "action_class": action_class,
            "granted_at": now,
            "expires_at": now + ttl_s,
            "decision_id": decision_id,
        }

    async def has_live_grant(self, agent_name: str, action_class: str, now: float | None = None) -> bool:
        """True if there is a live (not-yet-expired) grant for this agent +
        action class. A grant is a standing authorization until it expires
        (not single-use) -- the TTL is what bounds its blast radius."""
        now = time.time() if now is None else now
        async with self._db.execute(
            "SELECT 1 FROM execution_grants WHERE agent_name = ? AND action_class = ? "
            "AND expires_at > ? LIMIT 1",
            (agent_name, action_class, now),
        ) as cur:
            row = await cur.fetchone()
        return row is not None
