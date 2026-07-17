from __future__ import annotations

"""Store for user-to-user resource sharing.

Records that a user (owner) shared a resource with another user,
including what permission was granted and under what tier.

The Permissions app and the sharing consent loop read this table to
check whether a user may access a shared resource.  ``list_active_shares``
is the feed @taOSmd polls later.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_shares (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id       TEXT NOT NULL,
    resource_type       TEXT NOT NULL,
    resource_id         TEXT NOT NULL,
    shared_with_user_id TEXT NOT NULL,
    permission           TEXT NOT NULL,
    tier                TEXT NOT NULL DEFAULT 'once',
    granted_at          TEXT NOT NULL,
    expires_at          TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    UNIQUE(owner_user_id, resource_type, resource_id, shared_with_user_id, permission)
);
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class UserSharesStore(BaseStore):
    """Persistent store for user-to-user resource shares."""

    SCHEMA = SCHEMA

    # Serializes the DELETE-then-INSERT-then-SELECT in add_share.  The
    # 5-column UNIQUE on all-NOT-NULL columns would allow INSERT OR REPLACE,
    # but we keep the explicit delete+insert+select under this lock so two
    # concurrent same-key writes cannot interleave (one DELETE removing the
    # other's row before its SELECT-back returns it, or the second INSERT
    # hitting the unique).  The lock makes each write atomic against others
    # on this single connection.
    _write_lock: asyncio.Lock

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row
        self._write_lock = asyncio.Lock()

    async def _post_init(self) -> None:
        """Guarded schema upgrades for existing databases.

        Uses the PRAGMA table_info + ALTER TABLE pattern to add columns
        without destructive migration, matching the approach used by
        AgentGrantsStore and NotificationStore.
        """
        cols = {row[1] for row in await (await self._db.execute(
            "PRAGMA table_info(user_shares)")).fetchall()}
        # `status` column — guarded ALTER so existing databases gain it.
        # Existing rows (pre-status) are grandfathered as 'accepted' so
        # previously working shares don't break on upgrade.
        if "status" not in cols:
            await self._db.execute(
                "ALTER TABLE user_shares ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'"
            )
            await self._db.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def add_share(
        self,
        owner_user_id: str,
        resource_type: str,
        resource_id: str,
        shared_with_user_id: str,
        permission: str,
        *,
        tier: str = "once",
        expires_at: Optional[str] = None,
    ) -> dict:
        """Insert or replace a share for the exact 5-column key.

        Idempotent re-share: calling add_share again with the same
        (owner_user_id, resource_type, resource_id, shared_with_user_id,
        permission) tuple replaces the existing share rather than creating
        a duplicate.  The delete+insert+select runs under the write lock
        for atomicity.
        """
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised — call init() first")

        now = datetime.now(timezone.utc).isoformat()
        async with self._write_lock:
            # Remove any existing row for the exact key first.
            await self._db.execute(
                "DELETE FROM user_shares "
                "WHERE owner_user_id = ? AND resource_type = ? AND resource_id = ? "
                "AND shared_with_user_id = ? AND permission = ?",
                (owner_user_id, resource_type, resource_id,
                 shared_with_user_id, permission),
            )
            await self._db.execute(
                """
                INSERT INTO user_shares
                    (owner_user_id, resource_type, resource_id,
                     shared_with_user_id, permission, tier, granted_at, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_user_id, resource_type, resource_id,
                 shared_with_user_id, permission, tier, now, expires_at, 'pending'),
            )
            await self._db.commit()
            row = await (
                await self._db.execute(
                    "SELECT * FROM user_shares "
                    "WHERE owner_user_id = ? AND resource_type = ? "
                    "AND resource_id = ? AND shared_with_user_id = ? "
                    "AND permission = ?",
                    (owner_user_id, resource_type, resource_id,
                     shared_with_user_id, permission),
                )
            ).fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_shares(self, owner_user_id: str) -> list[dict]:
        """Return all shares owned by *owner_user_id*, ordered by granted_at."""
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM user_shares WHERE owner_user_id = ? ORDER BY granted_at",
            (owner_user_id,),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]

    async def list_shares_received(self, shared_with_user_id: str) -> list[dict]:
        """Return all shares where *shared_with_user_id* is the target."""
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM user_shares WHERE shared_with_user_id = ? "
            "ORDER BY granted_at",
            (shared_with_user_id,),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]

    async def list_active_shares(self) -> list[dict]:
        """Return all shares that are not yet expired.

        A share is active when ``expires_at IS NULL`` (never expires) or
        ``expires_at > now``.
        """
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised")
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "SELECT * FROM user_shares "
            "WHERE expires_at IS NULL OR expires_at > ? "
            "ORDER BY owner_user_id, resource_type, resource_id",
            (now,),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]

    async def revoke_share(self, share_id: int) -> None:
        """Delete a share by its id.

        No error is raised if the share does not exist — the delete is
        silently a no-op in that case.
        """
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised")
        await self._db.execute(
            "DELETE FROM user_shares WHERE id = ?",
            (share_id,),
        )
        await self._db.commit()

    async def user_can_access(
        self, resource_type: str, resource_id: str, user_id: str
    ) -> bool:
        """Return True if there is at least one active, non-expired share
        for *user_id* on (*resource_type*, *resource_id*)."""
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised")
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "SELECT 1 FROM user_shares "
            "WHERE resource_type = ? AND resource_id = ? "
            "AND shared_with_user_id = ? "
            "AND status = 'accepted' "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "LIMIT 1",
            (resource_type, resource_id, user_id, now),
        )
        row = await cursor.fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Accept / Deny (consent gate)
    # ------------------------------------------------------------------

    async def accept_share(self, share_id: int) -> dict | None:
        """Accept a pending share by id. Returns the updated row or None if not found."""
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised")
        await self._db.execute(
            "UPDATE user_shares SET status = 'accepted' WHERE id = ? AND status = 'pending'",
            (share_id,),
        )
        await self._db.commit()
        cursor = await self._db.execute(
            "SELECT * FROM user_shares WHERE id = ?", (share_id,)
        )
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    async def deny_share(self, share_id: int) -> dict | None:
        """Deny a pending share by id. Returns the updated row or None if not found."""
        if self._db is None:
            raise RuntimeError("UserSharesStore not initialised")
        await self._db.execute(
            "UPDATE user_shares SET status = 'denied' WHERE id = ? AND status = 'pending'",
            (share_id,),
        )
        await self._db.commit()
        cursor = await self._db.execute(
            "SELECT * FROM user_shares WHERE id = ?", (share_id,)
        )
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

