from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path

import aiosqlite

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sharing_grants (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    grantee TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    UNIQUE(artifact_id, grantee)
);
CREATE INDEX IF NOT EXISTS idx_sharing_grants_owner ON sharing_grants(owner_id);
CREATE INDEX IF NOT EXISTS idx_sharing_grants_grantee ON sharing_grants(grantee);
CREATE INDEX IF NOT EXISTS idx_sharing_grants_status ON sharing_grants(status);
"""


def _new_grant_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    return f"sg-{suffix}"


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class SharingGrantStore(BaseStore):
    SCHEMA = SCHEMA

    _write_lock: asyncio.Lock

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row
        self._write_lock = asyncio.Lock()

    async def grant(
        self,
        artifact_id: str,
        artifact_kind: str,
        owner_id: str,
        grantee: str,
    ) -> dict:
        """Create or reactivate a sharing grant.

        If an active grant already exists for (artifact_id, grantee), return it.
        If a revoked grant exists, reactivate it (status -> active, clear revoked_at).
        """
        if self._db is None:
            raise RuntimeError("SharingGrantStore not initialised — call init() first")

        if artifact_kind not in ("app", "game", "project", "workflow", "studio"):
            raise ValueError(f"invalid artifact_kind: {artifact_kind}")

        now = int(time.time())
        async with self._write_lock:
            existing = await (
                await self._db.execute(
                    "SELECT * FROM sharing_grants WHERE artifact_id = ? AND grantee = ?",
                    (artifact_id, grantee),
                )
            ).fetchone()

            if existing is not None:
                if existing["status"] == "active":
                    return _row_to_dict(existing)
                # Reactivate revoked grant
                await self._db.execute(
                    "UPDATE sharing_grants SET status = 'active', revoked_at = NULL WHERE id = ?",
                    (existing["id"],),
                )
                await self._db.commit()
                row = await (
                    await self._db.execute(
                        "SELECT * FROM sharing_grants WHERE id = ?", (existing["id"],)
                    )
                ).fetchone()
                return _row_to_dict(row)

            # Create new grant
            for _ in range(8):
                grant_id = _new_grant_id()
                async with self._db.execute(
                    "SELECT 1 FROM sharing_grants WHERE id = ?", (grant_id,)
                ) as cur:
                    if await cur.fetchone() is None:
                        break
            else:
                raise RuntimeError("could not allocate grant id")

            row = {
                "id": grant_id,
                "artifact_id": artifact_id,
                "artifact_kind": artifact_kind,
                "owner_id": owner_id,
                "grantee": grantee,
                "status": "active",
                "created_at": now,
                "revoked_at": None,
            }
            await self._db.execute(
                """INSERT INTO sharing_grants
                   (id, artifact_id, artifact_kind, owner_id, grantee, status, created_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["artifact_id"],
                    row["artifact_kind"],
                    row["owner_id"],
                    row["grantee"],
                    row["status"],
                    row["created_at"],
                    row["revoked_at"],
                ),
            )
            await self._db.commit()
            return row

    async def revoke(self, grant_id: str) -> dict | None:
        """Revoke a grant by id. Returns the updated row or None if not found."""
        if self._db is None:
            raise RuntimeError("SharingGrantStore not initialised — call init() first")

        now = int(time.time())
        async with self._write_lock:
            cursor = await self._db.execute(
                "UPDATE sharing_grants SET status = 'revoked', revoked_at = ? WHERE id = ? AND status = 'active'",
                (now, grant_id),
            )
            await self._db.commit()
            if cursor.rowcount == 0:
                return None
            row = await (
                await self._db.execute(
                    "SELECT * FROM sharing_grants WHERE id = ?", (grant_id,)
                )
            ).fetchone()
        return _row_to_dict(row) if row else None

    async def list_owned(self, owner_id: str) -> list[dict]:
        """Return all grants created by this owner (for the App Studio sharing section)."""
        if self._db is None:
            raise RuntimeError("SharingGrantStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM sharing_grants WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]

    async def list_for(self, grantee: str) -> list[dict]:
        """Return ACTIVE grants shared with this user (by username or email)."""
        if self._db is None:
            raise RuntimeError("SharingGrantStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM sharing_grants WHERE grantee = ? AND status = 'active' ORDER BY created_at DESC",
            (grantee,),
        )
        return [_row_to_dict(r) for r in await cursor.fetchall()]

    async def get(self, grant_id: str) -> dict | None:
        """Return a single grant by id, or None if not found."""
        if self._db is None:
            raise RuntimeError("SharingGrantStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM sharing_grants WHERE id = ?", (grant_id,)
        )
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None