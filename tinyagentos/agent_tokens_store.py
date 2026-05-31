from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT    NOT NULL,
    token       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    revoked_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_agent_active_token
    ON agent_tokens(agent_name)
    WHERE revoked_at IS NULL;
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    """Convert an aiosqlite.Row to a plain dict."""
    return {key: row[key] for key in row.keys()}


class AgentTokenExistsError(Exception):
    """Raised when attempting to issue a token for an agent that already
    has an active (non-revoked) token."""

    def __init__(self, agent_name: str) -> None:
        super().__init__(
            f"Agent '{agent_name}' already has an active token. "
            f"Revoke the existing token before issuing a new one."
        )
        self.agent_name = agent_name


class AgentTokensStore(BaseStore):
    """Per-agent token store with multi-worker-safe issuance.

    Uses ``BEGIN IMMEDIATE`` so concurrent workers cannot bypass the
    unique partial index ``uniq_agent_active_token`` — the reservation
    is visible to all connections as soon as the transaction begins.
    """

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    async def issue(self, agent_name: str, token: str) -> dict:
        """Issue a new active token for *agent_name*.

        Returns the row dict on success.  Raises ``AgentTokenExistsError``
        when the agent already has an active (non-revoked) token.
        """
        if self._db is None:
            raise RuntimeError("AgentTokensStore not initialised — call init() first")

        now = datetime.now(timezone.utc).isoformat()

        try:
            # BEGIN IMMEDIATE acquires the reserved lock straight away so
            # other workers see the intent before we commit — no window
            # where an asyncio.Lock in a single process would be bypassed.
            await self._db.execute("BEGIN IMMEDIATE")
            await self._db.execute(
                "INSERT INTO agent_tokens (agent_name, token, created_at) "
                "VALUES (?, ?, ?)",
                (agent_name, token, now),
            )
            await self._db.commit()
        except aiosqlite.IntegrityError:
            await self._db.execute("ROLLBACK")
            raise AgentTokenExistsError(agent_name) from None
        except Exception:
            await self._db.execute("ROLLBACK")
            raise

        row = await (
            await self._db.execute(
                "SELECT id, agent_name, token, created_at, revoked_at "
                "FROM agent_tokens WHERE agent_name = ? AND revoked_at IS NULL",
                (agent_name,),
            )
        ).fetchone()

        if row is None:
            raise RuntimeError(f"Token for '{agent_name}' not found after issue")

        return _row_to_dict(row)

    async def revoke(self, agent_name: str) -> dict | None:
        """Revoke the active token for *agent_name*, if one exists.

        Returns the updated row dict, or ``None`` when no active token
        was found.
        """
        if self._db is None:
            raise RuntimeError("AgentTokensStore not initialised — call init() first")

        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            "UPDATE agent_tokens SET revoked_at = ? "
            "WHERE agent_name = ? AND revoked_at IS NULL",
            (now, agent_name),
        )
        await self._db.commit()

        row = await (
            await self._db.execute(
                "SELECT id, agent_name, token, created_at, revoked_at "
                "FROM agent_tokens WHERE agent_name = ? AND revoked_at = ?",
                (agent_name, now),
            )
        ).fetchone()

        return _row_to_dict(row) if row else None

    async def get_active(self, agent_name: str) -> dict | None:
        """Return the active (non-revoked) token row for *agent_name*,
        or ``None``."""
        if self._db is None:
            raise RuntimeError("AgentTokensStore not initialised — call init() first")

        row = await (
            await self._db.execute(
                "SELECT id, agent_name, token, created_at, revoked_at "
                "FROM agent_tokens WHERE agent_name = ? AND revoked_at IS NULL",
                (agent_name,),
            )
        ).fetchone()

        return _row_to_dict(row) if row else None
