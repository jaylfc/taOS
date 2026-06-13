"""Store for connected GitHub identities (OAuth device-flow tokens).

Tokens are encrypted at rest with the exact same Fernet helper the
``SecretsStore`` uses (``.secrets_key`` in the data dir) — no new crypto is
introduced. The token is NEVER returned by :meth:`list`; only
:meth:`get_token` (an internal helper) exposes the plaintext.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from tinyagentos.base_store import BaseStore
from tinyagentos.secrets import _decrypt, _encrypt

GITHUB_IDENTITIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS github_identities (
    id TEXT PRIMARY KEY,
    login TEXT NOT NULL,
    avatar_url TEXT NOT NULL DEFAULT '',
    token TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
"""


class GitHubIdentitiesStore(BaseStore):
    SCHEMA = GITHUB_IDENTITIES_SCHEMA

    def __init__(self, db_path: Path):
        super().__init__(db_path)
        # Reuse the secrets key dir (same .secrets_key) so tokens use the same
        # encryption key as the rest of taOS.
        self._key_dir: Path = Path(db_path).parent

    async def add(
        self,
        login: str,
        avatar_url: str,
        token: str,
        scopes: str = "",
    ) -> dict:
        """Store a new identity. Returns the public fields (never the token)."""
        identity_id = str(uuid.uuid4())
        now = int(time.time())
        encrypted = _encrypt(token, self._key_dir)
        await self._db.execute(
            "INSERT INTO github_identities (id, login, avatar_url, token, scopes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (identity_id, login, avatar_url, encrypted, scopes, now),
        )
        await self._db.commit()
        return {
            "id": identity_id,
            "login": login,
            "avatar_url": avatar_url,
            "created_at": now,
        }

    async def list(self) -> list[dict]:
        """Return all identities WITHOUT tokens (id/login/avatar_url/created_at)."""
        async with self._db.execute(
            "SELECT id, login, avatar_url, created_at FROM github_identities "
            "ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {"id": r[0], "login": r[1], "avatar_url": r[2], "created_at": r[3]}
            for r in rows
        ]

    async def get_token(self, identity_id: str) -> str | None:
        """Internal: return the decrypted token for *identity_id* or None."""
        async with self._db.execute(
            "SELECT token FROM github_identities WHERE id = ?", (identity_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return _decrypt(row[0], self._key_dir)

    async def delete(self, identity_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM github_identities WHERE id = ?", (identity_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0
