from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

import aiosqlite

from tinyagentos.base_store import BaseStore

_EXPIRY_SECS = 60 * 60
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
    redeemed_request_id  TEXT,
    display_name         TEXT,
    kind                 TEXT NOT NULL DEFAULT 'agent',
    pin_required         INTEGER NOT NULL DEFAULT 1,
    contact_id           TEXT
);
"""

MIGRATIONS: list = []

# Valid values for the invite kind column.
_VALID_KINDS = frozenset({"agent", "collab"})


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
        if "display_name" not in existing_cols:
            await self._db.execute(
                "ALTER TABLE project_invites ADD COLUMN display_name TEXT"
            )
            await self._db.commit()
        if "kind" not in existing_cols:
            await self._db.execute(
                "ALTER TABLE project_invites ADD COLUMN kind TEXT NOT NULL DEFAULT 'agent'"
            )
            await self._db.commit()
        if "pin_required" not in existing_cols:
            await self._db.execute(
                "ALTER TABLE project_invites ADD COLUMN pin_required INTEGER NOT NULL DEFAULT 1"
            )
            await self._db.commit()
        if "contact_id" not in existing_cols:
            await self._db.execute(
                "ALTER TABLE project_invites ADD COLUMN contact_id TEXT"
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
                   check_interval_secs: int, created_by: str,
                   display_name: str | None = None,
                   kind: str = "agent",
                   pin_required: bool = True,
                   contact_id: str | None = None,
                   ttl_secs: int | None = None) -> dict:
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")

        if kind not in _VALID_KINDS:
            raise ValueError(
                f"invalid invite kind: {kind!r} — must be one of {sorted(_VALID_KINDS)}"
            )

        # Human-collaborator invites carry no agent scopes — delegation arrives
        # later via the D-milestone handshake, never on the human invite.
        if kind == "collab" and scopes:
            raise ValueError(
                "collab invites must carry no scopes"
            )

        # The pending cap is per-scope: project-scoped invites are capped per
        # project, OS-level (project_id IS NULL) invites are capped as a group.
        # SQL ``= NULL`` never matches, so branch on IS NULL to keep the cap live
        # for OS-level invites too.
        if project_id is None:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM project_invites "
                "WHERE project_id IS NULL AND status = 'pending'",
            )
        else:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM project_invites "
                "WHERE project_id = ? AND status = 'pending'",
                (project_id,),
            )
        row = await cursor.fetchone()
        pending_count = row[0] if row else 0
        if pending_count >= _PENDING_CAP:
            scope_label = "OS-level" if project_id is None else f"project {project_id}"
            raise InvitePendingCapError(
                f"{scope_label} already has {_PENDING_CAP} pending invites"
            )

        # project_tasks binds the token to a project, so it is only forced for
        # project-scoped invites. An OS-level invite mints a chat-available
        # identity with no project grant, so it keeps exactly the requested scopes.
        scopes = list(dict.fromkeys(list(scopes)))
        if project_id is not None:
            scopes = list(dict.fromkeys(scopes + ["project_tasks"]))

        pin = self._generate_pin()
        pin_hash = hashlib.sha256(pin.encode()).hexdigest()
        now = _now()
        if ttl_secs is not None and ttl_secs <= 0:
            raise ValueError("ttl_secs must be positive")
        expires_ts = now + (ttl_secs if ttl_secs is not None else _EXPIRY_SECS)

        # 6-digit invite IDs have a non-trivial collision chance under load
        # (~12 % at 500 pending).  Retry on UNIQUE constraint violation so a
        # collision surfaces as a slight latency bump rather than a 500.
        max_retries = 5
        for attempt in range(max_retries):
            invite_id = self._generate_invite_id()
            try:
                await self._db.execute(
                    """
                    INSERT INTO project_invites
                        (invite_id, project_id, pin_hash, scopes, approval_mode,
                         check_interval_secs, created_by, created_ts, expires_ts, status,
                         display_name, kind, pin_required, contact_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
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
                        display_name,
                        kind,
                        int(pin_required),
                        contact_id,
                    ),
                )
                await self._db.commit()
                break
            except aiosqlite.IntegrityError as exc:
                # Only retry on UNIQUE/PRIMARY KEY constraint violations
                # (invite_id collision).  Other integrity errors re-raise.
                if getattr(exc, "sqlite_errorcode", 0) not in (19, 1555, 2067):
                    raise
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        "Failed to generate a unique invite ID "
                        f"after {max_retries} attempts"
                    ) from exc
                continue

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
                "display_name": display_name,
                "kind": kind,
                "pin_required": int(pin_required),
                "contact_id": contact_id,
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

    async def list_os_level(self) -> list[dict]:
        """List OS-level invites (project_id IS NULL), newest first, without the
        pin hash. Mirrors ``list_for_project`` for the project-less group."""
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM project_invites WHERE project_id IS NULL "
            "ORDER BY created_ts DESC",
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = self._row_to_dict(row)
            d.pop("pin_hash", None)
            result.append(d)
        return result

    async def list_pending_collab_for_contact(
        self, contact_id: str
    ) -> list[dict]:
        """List pending collab-kind invites addressed to a specific contact."""
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM project_invites "
            "WHERE kind = 'collab' AND contact_id = ? AND status = 'pending' "
            "ORDER BY created_ts DESC",
            (contact_id,),
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
            "UPDATE project_invites SET status = 'revoked' "
            "WHERE invite_id = ? AND status IN ('pending', 'expired')",
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

        # Atomically claim the invite (pending→claimed) rather than immediately
        # marking it redeemed.  The caller must flip claimed→redeemed on success
        # or roll back to pending on failure so a failed approve does not burn
        # the invite unrecoverably (issue #1993).
        cursor = await self._db.execute(
            "UPDATE project_invites SET status = 'claimed' WHERE invite_id = ? AND status = 'pending'",
            (invite_id,),
        )
        await self._db.commit()
        if cursor.rowcount != 1:
            raise InviteAlreadyRedeemedError("invite has already been redeemed")

        updated = await self._fetch_row(invite_id)
        return self._row_to_dict(updated)

    async def mark_redeemed(
        self, invite_id: str, redeemed_by: str, redeemed_request_id: str
    ) -> None:
        """Record who/what redeemed an invite for audit and flip claimed→redeemed
        (called by the redeem route after the auth-request has been created/approved).
        Best-effort from the caller's perspective; the status flip already happened in
        ``redeem``."""
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        await self._db.execute(
            """
            UPDATE project_invites
               SET redeemed_by = ?, redeemed_request_id = ?, status = 'redeemed'
             WHERE invite_id = ? AND status = 'claimed'
            """,
            (redeemed_by, redeemed_request_id, invite_id),
        )
        await self._db.commit()

    async def mark_accepted(self, invite_id: str, accepted_by: str) -> None:
        """Record who accepted a collab invite and flip pending→redeemed.

        Collab invites are minted as 'pending' (no redeem/PIN step on the inviter
        side — the PIN is delivered out of band to the invitee). This method
        transitions the invite directly from 'pending' to 'redeemed', or from
        'claimed' to 'redeemed' for invites that went through the claim flow first.
        """
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        await self._db.execute(
            """
            UPDATE project_invites
               SET redeemed_by = ?, status = 'redeemed'
             WHERE invite_id = ? AND status IN ('pending', 'claimed')
            """,
            (accepted_by, invite_id),
        )
        await self._db.commit()

    async def rollback_to_pending(self, invite_id: str) -> None:
        """Roll back a claimed invite to pending so the caller can retry after a
        failed approve (issue #1993).  Only touches invites in 'claimed' status."""
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        await self._db.execute(
            "UPDATE project_invites SET status = 'pending' WHERE invite_id = ? AND status = 'claimed'",
            (invite_id,),
        )
        await self._db.commit()

    async def mark_expired(self, invite_id: str) -> bool:
        """Mark a pending invite as expired. Returns True if a row was updated."""
        if self._db is None:
            raise RuntimeError("ProjectInviteStore not initialised")
        cursor = await self._db.execute(
            "UPDATE project_invites SET status = 'expired' WHERE invite_id = ? AND status = 'pending'",
            (invite_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def _fetch_row(self, invite_id: str) -> aiosqlite.Row | None:
        cursor = await self._db.execute(
            "SELECT * FROM project_invites WHERE invite_id = ?", (invite_id,)
        )
        return await cursor.fetchone()

    def _row_to_dict(self, row: aiosqlite.Row) -> dict:
        if row is None:
            return {}
        return dict(row)
