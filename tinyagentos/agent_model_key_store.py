from __future__ import annotations

"""Consent-key store for Agent-as-a-Model (decision 19).

The key IS the consent grant: an external OpenAI-compatible caller presents a
bearer token that maps to {issuing_user, agent_ids, scopes, optional expiry,
optional rate cap}. A key can only be minted through the user-mediated consent
flow, and revoking the grant kills the key, so there is no raw model
passthrough.

Security: the bearer token is shown to the user exactly once at mint time; only
its SHA-256 hash is stored, so a database leak never exposes live keys. This is
deliberately stricter than the internal LiteLLMKeyStore (which stores its
routing tokens in plaintext for the cross-process auth hook); these keys are
exposed to third parties.

MVP cut item 1 of ~/tinyagentos-private/specs/agent-as-a-model.md. The /v1
endpoint, scope enforcement, and rate limiting build on top of this; they are
NOT in this slice.
"""

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

# Recognisable in logs, never collides with LiteLLM's sk- or the internal
# sk-taos- routing keys.
_TOKEN_PREFIX = "sk-taosagent-"

# Agent ids are used as filesystem path components (opencode home dirs);
# must match the validator in routes/agent_model_keys.py.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_model_keys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash      TEXT NOT NULL UNIQUE,
    issuing_user  TEXT NOT NULL,
    agent_ids     TEXT NOT NULL,
    scopes        TEXT NOT NULL,
    rate_cap      INTEGER,
    created_at    TEXT NOT NULL,
    expires_at    TEXT,
    revoked       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_amk_user ON agent_model_keys(issuing_user);
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _normalize_ts(value: str) -> str:
    """Canonicalise a caller-supplied expiry to a UTC isoformat string.

    Expiry is enforced by comparing against datetime.now(timezone.utc).isoformat(),
    a lexicographic compare that is only sound when both sides are the same UTC
    format. A 'Z' suffix, a non-UTC offset, or a naive timestamp would otherwise
    sort wrong and silently mis-honour a security-critical key expiry, so parse
    the input, require it be timezone-aware, and store the canonical UTC form.
    """
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware (e.g. ...+00:00 or ...Z)")
    return dt.astimezone(timezone.utc).isoformat()


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    for f in ("agent_ids", "scopes"):
        try:
            d[f] = json.loads(d[f])
        except (ValueError, TypeError):
            d[f] = []
    d["revoked"] = bool(d["revoked"])
    return d


class AgentModelKeyStore(BaseStore):
    """Persistent store of Agent-as-a-Model consent keys (hash + binding)."""

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    async def mint(
        self,
        issuing_user: str,
        agent_ids: list[str],
        scopes: list[str],
        *,
        rate_cap: Optional[int] = None,
        expires_at: Optional[str] = None,
    ) -> tuple[str, dict]:
        """Mint a new consent key. Returns (plaintext_token, record).

        The plaintext is returned ONCE for the caller to show the user; only
        its hash is stored. The record excludes any secret.
        """
        if self._db is None:
            raise RuntimeError("AgentModelKeyStore not initialised — call init() first")
        if not agent_ids:
            raise ValueError("a consent key must grant at least one agent")
        for a in agent_ids:
            if not _AGENT_ID_RE.fullmatch(a):
                raise ValueError(
                    f"agent_id {a!r} contains invalid characters; "
                    "only A-Z, a-z, 0-9, '.', '_', '-' are allowed"
                )
        if expires_at is not None:
            expires_at = _normalize_ts(expires_at)
        token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            """
            INSERT INTO agent_model_keys
                (key_hash, issuing_user, agent_ids, scopes, rate_cap, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _hash(token), issuing_user, json.dumps(list(agent_ids)),
                json.dumps(list(scopes or [])), rate_cap, now, expires_at,
            ),
        )
        await self._db.commit()
        row = await (
            await self._db.execute(
                "SELECT * FROM agent_model_keys WHERE id = ?", (cur.lastrowid,)
            )
        ).fetchone()
        rec = _row_to_dict(row)  # type: ignore[arg-type]
        rec.pop("key_hash", None)  # never surface the hash to callers
        return token, rec

    async def resolve(self, token: str) -> Optional[dict]:
        """Resolve a presented bearer token to its binding.

        Returns None if the token is unknown, revoked, or expired, so an
        unauthorised caller is rejected without leaking which condition failed.
        """
        if self._db is None:
            raise RuntimeError("AgentModelKeyStore not initialised")
        now = datetime.now(timezone.utc).isoformat()
        row = await (
            await self._db.execute(
                "SELECT * FROM agent_model_keys "
                "WHERE key_hash = ? AND revoked = 0 "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (_hash(token), now),
            )
        ).fetchone()
        if row is None:
            return None
        rec = _row_to_dict(row)
        rec.pop("key_hash", None)
        return rec

    async def revoke(self, key_id: int) -> bool:
        """Revoke a key by its row id. Returns True if a row was revoked."""
        if self._db is None:
            raise RuntimeError("AgentModelKeyStore not initialised")
        cur = await self._db.execute(
            "UPDATE agent_model_keys SET revoked = 1 WHERE id = ? AND revoked = 0",
            (key_id,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def list_for_user(self, issuing_user: str) -> list[dict]:
        """Keys a user issued, newest first, without any secret material.

        Feeds the settings / Permissions surface where a user reviews and
        revokes their issued agent-as-a-model keys.
        """
        if self._db is None:
            raise RuntimeError("AgentModelKeyStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM agent_model_keys WHERE issuing_user = ? "
            "ORDER BY created_at DESC",
            (issuing_user,),
        )
        out = []
        for r in await cursor.fetchall():
            rec = _row_to_dict(r)
            rec.pop("key_hash", None)
            out.append(rec)
        return out
