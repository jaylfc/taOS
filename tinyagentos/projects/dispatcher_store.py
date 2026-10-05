"""Dispatcher config store - persisted per-user dispatcher configuration.

Part of Dispatcher S1 (jaylfc/taOS#2141). The dispatcher stays inert in S1;
nothing reads this config yet. Satisfies "Config CRUD (owner/admin only) +
persisted per user" and "Disabled by default; enabling requires explicit
user action".
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from tinyagentos.projects.ids import new_id
from tinyagentos.projects.tx import ProjectsDBStore

logger = logging.getLogger(__name__)


DISPATCHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatcher_config (
    user_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    boards TEXT NOT NULL DEFAULT '[]',
    eligible_agents TEXT NOT NULL DEFAULT '[]',
    max_concurrent_per_agent INTEGER NOT NULL DEFAULT 1,
    poll_seconds INTEGER NOT NULL DEFAULT 30,
    lease_seconds INTEGER NOT NULL DEFAULT 900,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatch_ledger (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    assignee_written TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'pending',
    reason TEXT NOT NULL DEFAULT '',
    assigned_at REAL NOT NULL,
    lease_expires_at REAL NOT NULL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_dispatch_ledger_task_state ON dispatch_ledger(task_id, state);
CREATE INDEX IF NOT EXISTS idx_dispatch_ledger_canonical_state ON dispatch_ledger(canonical_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dsp_one_pending ON dispatch_ledger(task_id) WHERE state = 'pending';

CREATE TABLE IF NOT EXISTS dispatch_agent_backoff (
    canonical_id TEXT PRIMARY KEY,
    consecutive_expiries INTEGER NOT NULL DEFAULT 0,
    next_eligible_at REAL NOT NULL DEFAULT 0,
    last_assigned_at REAL NOT NULL DEFAULT 0
);
"""


@dataclass
class DispatcherConfig:
    """Dispatcher configuration for a single user."""
    user_id: str
    enabled: bool = False
    boards: list[str] | None = None
    eligible_agents: list[str] | None = None
    max_concurrent_per_agent: int = 1
    poll_seconds: int = 30
    lease_seconds: int = 900
    updated_by: str = ""
    # None means "never stored": set_config stamps it on write. A synthesised
    # time.time() here would date a config the user never saved.
    updated_at: Optional[float] = None

    def __post_init__(self):
        if self.boards is None:
            self.boards = []
        if self.eligible_agents is None:
            self.eligible_agents = []

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "enabled": self.enabled,
            "boards": self.boards,
            "eligible_agents": self.eligible_agents,
            "max_concurrent_per_agent": self.max_concurrent_per_agent,
            "poll_seconds": self.poll_seconds,
            "lease_seconds": self.lease_seconds,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


@dataclass
class DispatchLedger:
    """Pending dispatch ledger row."""
    id: str
    user_id: str
    task_id: str
    project_id: str
    canonical_id: str
    assignee_written: str
    state: str = "pending"
    reason: str = ""
    assigned_at: float = 0.0
    lease_expires_at: float = 0.0
    resolved_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "canonical_id": self.canonical_id,
            "assignee_written": self.assignee_written,
            "state": self.state,
            "reason": self.reason,
            "assigned_at": self.assigned_at,
            "lease_expires_at": self.lease_expires_at,
            "resolved_at": self.resolved_at,
        }


@dataclass
class DispatchAgentBackoff:
    """Agent backoff state after consecutive expiries."""
    canonical_id: str
    consecutive_expiries: int = 0
    next_eligible_at: float = 0.0
    last_assigned_at: float = 0.0


def _row_to_config(row) -> DispatcherConfig:
    return DispatcherConfig(
        user_id=row[0],
        enabled=bool(row[1]),
        boards=json.loads(row[2]) if row[2] else [],
        eligible_agents=json.loads(row[3]) if row[3] else [],
        max_concurrent_per_agent=row[4],
        poll_seconds=row[5],
        lease_seconds=row[6],
        updated_by=row[7] if row[7] else "",
        updated_at=row[8] if row[8] else None,
    )


class DispatcherStore(ProjectsDBStore):
    """Per-user dispatcher configuration store backed by projects.db."""

    SCHEMA = DISPATCHER_SCHEMA

    async def _post_init(self) -> None:
        """No migrations needed for fresh tables."""
        pass

    async def get_config(self, user_id: str) -> DispatcherConfig:
        """Get dispatcher config for a user, returning defaults if no row exists.

        Defaults: enabled=False, boards=[], eligible_agents=[], max_concurrent_per_agent=1,
        poll_seconds=30, lease_seconds=900.
        """
        if not user_id:
            return DispatcherConfig(user_id=user_id)
        async with self._read(
            "SELECT user_id, enabled, boards, eligible_agents, max_concurrent_per_agent, "
            "poll_seconds, lease_seconds, updated_by, updated_at "
            "FROM dispatcher_config WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return DispatcherConfig(user_id=user_id)
            return _row_to_config(row)

    async def set_config(
        self,
        user_id: str,
        cfg: DispatcherConfig,
        updated_by: str,
    ) -> DispatcherConfig:
        """Upsert dispatcher config for a user."""
        if not user_id:
            raise ValueError("user_id is required")
        now = time.time()
        # Validate max_concurrent_per_agent == 1 (enforced by store, not clamped)
        if cfg.max_concurrent_per_agent != 1:
            raise ValueError(
                "max_concurrent_per_agent must be exactly 1 (one-active-claim rule)"
            )
        # Clamp poll_seconds to 10..3600
        poll_seconds = max(10, min(3600, cfg.poll_seconds))
        # Clamp lease_seconds to 60..86400
        lease_seconds = max(60, min(86400, cfg.lease_seconds))

        async with self._tx():
            await self._db.execute(
                """
                INSERT INTO dispatcher_config
                    (user_id, enabled, boards, eligible_agents, max_concurrent_per_agent,
                     poll_seconds, lease_seconds, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    boards = excluded.boards,
                    eligible_agents = excluded.eligible_agents,
                    max_concurrent_per_agent = excluded.max_concurrent_per_agent,
                    poll_seconds = excluded.poll_seconds,
                    lease_seconds = excluded.lease_seconds,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    1 if cfg.enabled else 0,
                    json.dumps(cfg.boards or []),
                    json.dumps(cfg.eligible_agents or []),
                    cfg.max_concurrent_per_agent,
                    poll_seconds,
                    lease_seconds,
                    updated_by,
                    now,
                ),
            )
        return await self.get_config(user_id)

    async def list_configs(self) -> list[DispatcherConfig]:
        """List all dispatcher configs (admin view)."""
        async with self._read(
            "SELECT user_id, enabled, boards, eligible_agents, max_concurrent_per_agent, "
            "poll_seconds, lease_seconds, updated_by, updated_at "
            "FROM dispatcher_config ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_config(r) for r in rows]

    async def insert_lease(
        self,
        task_id: str,
        project_id: str,
        user_id: str,
        assignee_written: str,
        lease_expires_at: float,
    ) -> str:
        """Insert a pending ledger row.

        Returns the generated lease ID.
        """
        lease_id = new_id()
        now = time.time()

        async with self._tx():
            await self._db.execute(
                """
                INSERT INTO dispatch_ledger (
                    id, user_id, task_id, project_id, canonical_id,
                    assignee_written, state, reason, assigned_at, lease_expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease_id,
                    user_id,
                    task_id,
                    project_id,
                    task_id,  # canonical_id for now
                    assignee_written,
                    "pending",
                    "",
                    now,
                    lease_expires_at,
                ),
            )

            # Stamp backoff for the task
            await self.stamp_backoff(task_id, now)

        return lease_id

    async def pending_for_agent(
        self,
        agent_canonical_id: str,
        now: Optional[float] = None,
    ) -> list[DispatchLedger]:
        """Get pending leases for an agent.

        Args:
            agent_canonical_id: The agent's canonical ID
            now: Current time, defaults to time.time()

        Returns:
            List of pending DispatchLedger rows where assignee_written matches agent_canonical_id
        """
        if now is None:
            now = time.time()

        async with self._read(
            """
            SELECT id, user_id, task_id, project_id, canonical_id,
                   assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at
            FROM dispatch_ledger
            WHERE assignee_written = ? AND state = 'pending'
            ORDER BY assigned_at ASC
            """,
            (agent_canonical_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [_row_to_ledger(r) for r in rows]

    async def expired_counts_48h(
        self,
        now: Optional[float] = None,
    ) -> dict[str, int]:
        """Get expired lease counts per canonical ID in the last 48 hours.

        Args:
            now: Current time, defaults to time.time()

        Returns:
            Dict mapping canonical_id to count of expired leases
        """
        if now is None:
            now = time.time()

        cutoff = now - (48 * 3600)

        async with self._read(
            """
            SELECT assignee_written, COUNT(*)
            FROM dispatch_ledger
            WHERE state = 'pending' AND lease_expires_at < ?
            GROUP BY assignee_written
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
            return {row[0]: row[1] for row in rows}

    async def recent_expiries(
        self,
        now: Optional[float] = None,
    ) -> list[tuple[str, str, float]]:
        """Get recently expired leases.

        Args:
            now: Current time, defaults to time.time()

        Returns:
            List of (canonical_id, task_id, expired_at) tuples for expired leases
        """
        if now is None:
            now = time.time()

        cutoff = now - (48 * 3600)

        async with self._read(
            """
            SELECT assignee_written, task_id, lease_expires_at
            FROM dispatch_ledger
            WHERE state = 'pending' AND lease_expires_at < ?
            ORDER BY lease_expires_at DESC
            """,
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
            return [(row[0], row[1], row[2]) for row in rows]

    async def stamp_backoff(self, task_id: str, now: Optional[float] = None) -> None:
        """Stamp backoff for a task after assignment.

        Args:
            task_id: The task ID
            now: Current time, defaults to time.time()
        """
        if now is None:
            now = time.time()

        async with self._tx():
            # Get the canonical_id for this task (assuming task_id == canonical_id)
            async with self._read(
                "SELECT assignee_written FROM dispatch_ledger WHERE task_id = ? AND state = 'pending'",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
                if row is None:
                    return

                canonical_id = row[0]

            # Update or insert backoff record
            await self._db.execute(
                """
                INSERT INTO dispatch_agent_backoff (
                    canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at
                )
                VALUES (?, 1, ?, ?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                    consecutive_expiries = excluded.consecutive_expiries,
                    next_eligible_at = GREATEST(
                        dispatch_agent_backoff.next_eligible_at,
                        excluded.next_eligible_at
                    ),
                    last_assigned_at = excluded.last_assigned_at
                """,
                (
                    canonical_id,
                    now,  # next_eligible_at: reset immediately for now
                    now,  # last_assigned_at
                ),
            )