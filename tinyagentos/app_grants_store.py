from __future__ import annotations

"""Store for per-app capability grants (app permission system, #56).

A grant records that a user decided (granted or denied) a specific capability
for an installed app, mirroring AgentGrantsStore for agents. This store is the
persistence layer of decision 6 (manifest declares capabilities, user grants
them via the Decisions/consent flow); it is intentionally vocabulary-agnostic:
the closed APP_CAPABILITIES vocabulary and its validation live at the
manifest-validation / grant-request layer (a later slice, pending Jay's v1
vocabulary in pending-decisions item 23), not here.

This table holds the CURRENT decision per (user, app, capability), not a full
history: re-deciding a capability overwrites the row (last-write-wins), and
revoke flips a grant to 'denied' rather than deleting it, so the capability is
no longer granted while the row persists as a denial. A true append-only log of
every grant/revoke event (per the #103/#105 audit-trail direction) is a
separate enhancement, not this MVP. The Permissions app (decision 20) reads +
revokes here.
"""

from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_grants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    app_id      TEXT NOT NULL,
    capability  TEXT NOT NULL,
    decision    TEXT NOT NULL DEFAULT 'granted',
    decided_at  TEXT NOT NULL,
    UNIQUE (user_id, app_id, capability)
);
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class AppGrantsStore(BaseStore):
    """Persistent store for per-app capability grants, scoped by user."""

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    async def set_decision(
        self,
        user_id: str,
        app_id: str,
        capability: str,
        *,
        decision: str = "granted",
    ) -> dict:
        """Record a grant or denial for (user_id, app_id, capability).

        Idempotent via INSERT OR REPLACE, so re-deciding a capability (e.g. a
        user granting something they previously denied) overwrites in place.
        """
        if self._db is None:
            raise RuntimeError("AppGrantsStore not initialised — call init() first")
        if decision not in ("granted", "denied"):
            raise ValueError(f"invalid decision: {decision}")
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO app_grants
                (user_id, app_id, capability, decision, decided_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, app_id, capability, decision, now),
        )
        await self._db.commit()
        row = await (
            await self._db.execute(
                "SELECT * FROM app_grants "
                "WHERE user_id = ? AND app_id = ? AND capability = ?",
                (user_id, app_id, capability),
            )
        ).fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]

    async def revoke(self, user_id: str, app_id: str, capability: str) -> None:
        """Revoke a previously granted capability by recording a denial.

        The row is kept (flipped to 'denied'), not deleted, so the capability
        stops being granted without losing that a decision exists. A no-op if
        the capability was never decided for this (user, app).
        """
        if self._db is None:
            raise RuntimeError("AppGrantsStore not initialised")
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE app_grants SET decision = 'denied', decided_at = ? "
            "WHERE user_id = ? AND app_id = ? AND capability = ?",
            (now, user_id, app_id, capability),
        )
        await self._db.commit()

    async def list_grants(self, user_id: str, app_id: str) -> list[dict]:
        """Every recorded decision (granted + denied) for (user_id, app_id)."""
        if self._db is None:
            raise RuntimeError("AppGrantsStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM app_grants WHERE user_id = ? AND app_id = ? "
            "ORDER BY decided_at",
            (user_id, app_id),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]

    async def granted_capabilities(self, user_id: str, app_id: str) -> set[str]:
        """The currently-granted capabilities for (user_id, app_id).

        This is the enforcement query: a privileged SDK call checks the
        capability against this set. Denied/revoked capabilities are excluded.
        """
        if self._db is None:
            raise RuntimeError("AppGrantsStore not initialised")
        cursor = await self._db.execute(
            "SELECT capability FROM app_grants "
            "WHERE user_id = ? AND app_id = ? AND decision = 'granted'",
            (user_id, app_id),
        )
        return {r["capability"] for r in await cursor.fetchall()}
