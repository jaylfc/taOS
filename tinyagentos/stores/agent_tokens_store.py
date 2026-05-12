"""Per-agent API token store — opaque random tokens, SHA-256 hashed at rest.

Tokens authenticate agents calling the REST API. One active token per
agent at a time; issuing a new token atomically revokes the previous one.
Lookup hashes the bearer and matches against `token_hash`, scoped to
`revoked_at IS NULL`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_TOKEN_PREFIX = "taos_agent_"


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _generate_plaintext() -> str:
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentTokensStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_tokens (
                    token_hash    TEXT PRIMARY KEY,
                    agent_id      TEXT NOT NULL,
                    user_id       TEXT NOT NULL,
                    scope_json    TEXT NOT NULL,
                    issued_at     TEXT NOT NULL,
                    revoked_at    TEXT,
                    last_used_at  TEXT
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_agent_id ON agent_tokens(agent_id)")
            await db.commit()

    async def close(self) -> None:
        pass

    async def issue(
        self,
        *,
        agent_id: str,
        user_id: str,
        scope: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Issue a new token for an agent. Atomically revokes any prior active token."""
        async with self._lock:
            plaintext = _generate_plaintext()
            token_hash = _hash(plaintext)
            now = _now_iso()
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE agent_tokens SET revoked_at = ? WHERE agent_id = ? AND revoked_at IS NULL",
                    (now, agent_id),
                )
                await db.execute(
                    "INSERT INTO agent_tokens (token_hash, agent_id, user_id, scope_json, issued_at) VALUES (?, ?, ?, ?, ?)",
                    (token_hash, agent_id, user_id, json.dumps(scope), now),
                )
                await db.commit()
            row = {
                "token_hash": token_hash,
                "agent_id": agent_id,
                "user_id": user_id,
                "scope": scope,
                "issued_at": now,
                "revoked_at": None,
                "last_used_at": None,
            }
            return plaintext, row

    async def lookup_by_plaintext(self, plaintext: str) -> dict[str, Any] | None:
        """Look up an active token. Returns None if not found or revoked."""
        if not plaintext.startswith(_TOKEN_PREFIX):
            return None
        token_hash = _hash(plaintext)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM agent_tokens WHERE token_hash = ? AND revoked_at IS NULL",
                (token_hash,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return {
                "token_hash": row["token_hash"],
                "agent_id": row["agent_id"],
                "user_id": row["user_id"],
                "scope": json.loads(row["scope_json"]),
                "issued_at": row["issued_at"],
                "revoked_at": row["revoked_at"],
                "last_used_at": row["last_used_at"],
            }
