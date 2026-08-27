from __future__ import annotations

"""SQLite-backed store for password-reset tokens.

Token lifecycle:

  - ``mint`` generates a cryptographically-random reset token, persists ONLY
    its SHA-256 hash, and returns the plaintext token once so the caller (the
    ``POST /api/password/request`` route plus the SMTP slice) can deliver it
    out-of-band. The plaintext token never touches the database and is never
    logged.

  - ``get_by_token_hash`` resolves a token by hash ALONE (no ``user_id``).
    A reset flow is unauthenticated, so the caller has no user identity yet;
    the owning ``user_id`` is recovered from the token.

  - ``is_valid`` checks existence, the single-use flag, and the TTL.

  - ``consume`` marks a token used in ONE atomic ``UPDATE ... WHERE used=0``,
    so two concurrent consumes cannot both win a read-then-write race.

  - Minting a new token for a user atomically invalidates that user's prior
    unused tokens (``UPDATE ... SET used=1 WHERE user_id=? AND used=0``) so a
    re-issued link cannot be replayed.

Single class, single source of truth for storage: routes must not define
their own token storage methods.
"""

import hashlib
import secrets
import time
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

#: Server-side TTL for a reset token: 30 minutes. Matches the window the
#: SMTP slice is told the link is good for; never persisted or trusted from
#: the client.
PASSWORD_RESET_TTL_SECONDS = 1800

SCHEMA = """
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class PasswordResetStore(BaseStore):
    """Persistent store for password-reset tokens.

    Exactly one place owns token storage so routes cannot drift into a
    split-brain implementation. Tokens are stored only as SHA-256 hashes;
    the plaintext is returned from ``mint`` for one-shot delivery and is
    never persisted or logged.
    """

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    @staticmethod
    def hash_token(token: str) -> str:
        """Return the SHA-256 hex digest of a plaintext reset token.

        Shared by ``mint`` (write side) and the route that validates a token
        supplied via the reset link, so the hashing scheme cannot drift
        between mint and consume. The plaintext is never stored.
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def mint(
        self,
        user_id: str,
        *,
        ttl_seconds: int = PASSWORD_RESET_TTL_SECONDS,
    ) -> str:
        """Mint a fresh reset token for *user_id*.

        Returns the plaintext token (for out-of-band delivery, e.g. email).
        Only the SHA-256 hash is persisted. Any prior UNUSED token for this
        user is atomically invalidated so a re-issued link cannot be
        replayed after a new one is generated.
        """
        if self._db is None:
            raise RuntimeError("PasswordResetStore not initialised -- call init() first")
        if not user_id:
            raise ValueError("user_id is required")

        token = secrets.token_urlsafe(32)
        token_hash = self.hash_token(token)
        now = int(time.time())
        expires_at = now + int(ttl_seconds)

        # Invalidate prior unused tokens for this user in ONE atomic UPDATE,
        # so a previously-issued (and possibly leaked) link is dead before the
        # new one is handed out.
        await self._db.execute(
            "UPDATE password_resets SET used = 1 "
            "WHERE user_id = ? AND used = 0",
            (user_id,),
        )
        await self._db.execute(
            "INSERT INTO password_resets "
            "(token_hash, user_id, created_at, expires_at, used) "
            "VALUES (?, ?, ?, ?, 0)",
            (token_hash, user_id, now, expires_at),
        )
        await self._db.commit()
        return token

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_token_hash(self, token_hash: str) -> Optional[dict]:
        """Return the row for *token_hash*, looked up by hash ALONE.

        No ``user_id`` parameter: a reset flow is unauthenticated, so the
        caller cannot supply one. Returning the row lets the route recover the
        owning ``user_id`` from the token itself.
        """
        if self._db is None:
            raise RuntimeError("PasswordResetStore not initialised -- call init() first")
        row = await (
            await self._db.execute(
                "SELECT token_hash, user_id, created_at, expires_at, used "
                "FROM password_resets WHERE token_hash = ?",
                (token_hash,),
            )
        ).fetchone()
        return _row_to_dict(row) if row else None

    async def is_valid(self, token_hash: str) -> bool:
        """True iff the token exists, is unused, and is within its TTL."""
        if self._db is None:
            raise RuntimeError("PasswordResetStore not initialised -- call init() first")
        row = await self.get_by_token_hash(token_hash)
        if row is None:
            return False
        now = int(time.time())
        return row["used"] == 0 and row["expires_at"] > now

    # ------------------------------------------------------------------
    # Consume
    # ------------------------------------------------------------------

    async def consume(self, token_hash: str) -> bool:
        """Atomically consume a token.

        ONE ``UPDATE ... WHERE used=0`` (plus the TTL guard) -- no
        read-then-write. Returns True iff the token was still unused and
        unexpired and is now marked used. Two concurrent consumes cannot both
        win: the conditional UPDATE matches at most one row.
        """
        if self._db is None:
            raise RuntimeError("PasswordResetStore not initialised -- call init() first")
        now = int(time.time())
        cur = await self._db.execute(
            "UPDATE password_resets SET used = 1 "
            "WHERE token_hash = ? AND used = 0 AND expires_at > ?",
            (token_hash, now),
        )
        await self._db.commit()
        return cur.rowcount > 0
