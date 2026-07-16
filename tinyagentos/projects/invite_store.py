from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

import aiosqlite

from tinyagentos.base_store import BaseStore

_EXPIRY_SECS = 15 * 60
_MAX_ATTEMPTS = 5
_PENDING_CAP = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_invites (
    invite_id            TEXT PRIMARY KEY,
    project_id           TEXT,
    pin_hash             TEXT NOT NULL,
    scopes               TEXT NOT NULL,
    approval_mode        TEXT NOT NULL,
    check_interval_secs  INTEGER,
    created_by           TEXT NOT NULL,
    created_ts           REAL NOT NULL,
    expires_ts           REAL NOT NULL,
    redeem_attempts      INTEGER DEFAULT 0,
    status               TEXT NOT NULL,
    redeemed_by          TEXT,
    redeemed_request_id  TEXT
);
"""

MIGRATIONS: list = []


class InvitePinError(Exception):
    pass


class InviteExpiredError(Exception):
    pass


class InviteAlreadyRedeemedError(Exception):
    pass


class InvitePendingCapError(Exception):
    pass


class InviteRevokedError(Exception):
    pass


def _now() -> float:
    return time.time()


class ProjectInviteStore(BaseStore):
    SCHEMA = SCHEMA
    MIGRATIONS = MIGRATIONS

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    async def _post_init(self) -> None:
        await super()._post_init()
        if self._db is None:
            return
        existing_cols = {
            row[1]
            for row in await (
                await self._db.execute("PRAGMA table_info(project_invites)")
            ).fetchall()
        }
        if "redeemed_request_id" not in existing_cols:
            await self._db.execute(
                "ALTER TABLE project_invites ADD COLUMN redeemed_request_id TEXT"
            )
            await self._db.commit()
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_invites_project "
            "ON project_invites(project_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_invites_status "
            "ON project_invites(status)"
        )
        await self._db.commit()

    def _generate_invite_id(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def _generate_pin(self) -> str:
        return f"{secrets.randbelow(10_000):04d}"

    async def mint(self, *, project_id=None, scopes: list[str], approval_mode: str,
                   check_interval_secs: int, created_by: str) -> dict:
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM project_invites WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        )
        row = await cursor.fetchone()
        pending_count = row[0] if row else 0
        if pending_count >= _PENDING_CAP:
            raise InvitePendingCapError(
                f"project {project_id} already has {_PENDING_CAP} pending invites"
            )

        scopes = list(dict.fromkeys(list(scopes) + ["project_tasks"]))

        invite_id = self._generate_invite_id()
        pin = self._generate_pin()
        pin_hash = hashlib.sha256(pin.encode()).hexdigest()
        now = _now()
        expires_ts = now + _EXPIRY_SECS

        await self._db.execute(
            """
            INSERT INTO project_invites
                (invite_id, project_id, pin_hash, scopes, approval_mode,
                 check_interval_secs, created_by, created_ts, expires_ts, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                invite_id,
                project_id,
                pin_hash,
                json.dumps(scopes),
                approval_mode,
                check_interval_secs,
                created_by,
                now,
                expires_ts,
            ),
        )
        await self._db.commit()

        return {
            "record": {
                "invite_id": invite_id,
                "project_id": project_id,
                "pin_hash": pin_hash,
                "scopes": scopes,
                "approval_mode": approval_mode,
                "check_interval_secs": check_interval_secs,
                "created_by": created_by,
                "created_ts": now,
                "expires_ts": expires_ts,
                "redeem_attempts": 0,
                "status": "pending",
                "redeemed_by": None,
                "redeemed_request_id": None,
            },
            "pin": pin,
        }

    async def get(self, invite_id: str) -> dict | None:
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        row = await self._fetch_row(invite_id)
        if row is None:
            return None
        if row["status"] == "pending" and row["expires_ts"] is not None:
            if _now() > row["expires_ts"]:
                await self._db.execute(
                    "UPDATE project_invites SET status = 'expired' WHERE invite_id = ?",
                    (invite_id,),
                )
                await self._db.commit()
                row = await self._fetch_row(invite_id)
        return self._row_to_dict(row)

    async def list_for_project(self, project_id: str) -> list[dict]:
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM project_invites WHERE project_id = ? ORDER BY created_ts DESC",
            (project_id,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = self._row_to_dict(row)
            d.pop("pin_hash", None)
            result.append(d)
        return result

    async def revoke(self, invite_id: str) -> bool:
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        cursor = await self._db.execute(
            "UPDATE project_invites SET status = 'revoked' WHERE invite_id = ? AND status = 'pending'",
            (invite_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def redeem(self, invite_id: str, pin: str) -> dict:
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        row = await self._fetch_row(invite_id)
        if row is None:
            raise InvitePinError("invalid invite id or pin")

        status = row["status"]
        if status == "revoked":
            raise InviteRevokedError("invite has been revoked")
        if status == "redeemed":
            raise InviteAlreadyRedeemedError("invite has already been redeemed")

        if row["expires_ts"] is not None and _now() > row["expires_ts"]:
            await self._db.execute(
                "UPDATE project_invites SET status = 'expired' WHERE invite_id = ?",
                (invite_id,),
            )
            await self._db.commit()
            raise InviteExpiredError("invite has expired")

        attempts = row["redeem_attempts"] or 0
        if attempts >= _MAX_ATTEMPTS:
            raise InvitePinError("invalid invite id or pin")

        pin_hash = hashlib.sha256(pin.encode()).hexdigest()
        if not hmac.compare_digest(pin_hash, row["pin_hash"] or ""):
            await self._db.execute(
                "UPDATE project_invites SET redeem_attempts = redeem_attempts + 1 WHERE invite_id = ?",
                (invite_id,),
            )
            await self._db.commit()
            new_attempts = attempts + 1
            if new_attempts >= _MAX_ATTEMPTS:
                await self._db.execute(
                    "UPDATE project_invites SET status = 'expired' WHERE invite_id = ?",
                    (invite_id,),
                )
                await self._db.commit()
            raise InvitePinError("invalid invite id or pin")

        cursor = await self._db.execute(
            "UPDATE project_invites SET status = 'redeemed' WHERE invite_id = ? AND status = 'pending'",
            (invite_id,),
        )
        await self._db.commit()
        if cursor.rowcount != 1:
            raise InviteAlreadyRedeemedError("invite has already been redeemed")

        updated = await self._fetch_row(invite_id)
        return self._row_to_dict(updated)

    async def _fetch_row(self, invite_id: str) -> aiosqlite.Row | None:
        cursor = await self._db.execute(
            "SELECT * FROM project_invites WHERE invite_id = ?", (invite_id,)
        )
        return await cursor.fetchone()

    def _row_to_dict(self, row: aiosqlite.Row) -> dict:
        if row is None:
            return {}
        return dict(row)
