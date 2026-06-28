"""In-house per-agent LiteLLM key store.

The alternative to LiteLLM's Postgres/prisma virtual-key table. The
controller mints a random per-agent token here with its allowed model
list; the LiteLLM ``custom_auth`` hook (``tinyagentos.litellm_auth``)
reads the same store to authorize incoming requests. Both processes open
the same SQLite file, so this works with NO ``DATABASE_URL`` and no prisma
(the whole point: virtual keys on ARM / no-Postgres installs).

Scope intentionally tiny: mint, lookup, re-scope, delete. Spend/budget
tracking stays in taOS's existing trace/observability layer, not here.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_keys (
    token           TEXT PRIMARY KEY,
    agent           TEXT NOT NULL,
    allowed_models  TEXT NOT NULL,
    created_ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_keys_agent ON agent_keys(agent);
"""

# taos-prefixed so the key is recognisable in logs and never collides with
# LiteLLM's own sk- keys.
_TOKEN_PREFIX = "sk-taos-"


class LiteLLMKeyStore:
    """SQLite-backed per-agent key store, safe for cross-process read/write.

    The controller writes (mint/rescope/delete); the LiteLLM subprocess's
    auth hook only reads. WAL mode keeps a concurrent reader from blocking
    on a writer.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def mint(self, agent: str, allowed_models: list[str] | None) -> str:
        """Create a fresh token for an agent scoped to allowed_models.

        An empty/None allowlist is stored as ``[]`` (deny-all rather than a
        bogus "default" alias) so the hook can enforce it explicitly.
        """
        token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        models_json = json.dumps(list(allowed_models or []))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_keys (token, agent, allowed_models, created_ts) "
                "VALUES (?, ?, ?, ?)",
                (token, agent, models_json, time.time()),
            )
        return token

    def lookup(self, token: str) -> dict | None:
        """Return ``{agent, allowed_models}`` for a token, or None if unknown."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT agent, allowed_models FROM agent_keys WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        try:
            models = json.loads(row["allowed_models"])
        except (ValueError, TypeError):
            models = []
        return {"agent": row["agent"], "allowed_models": models}

    def set_models(self, token: str, allowed_models: list[str]) -> bool:
        """Re-scope a token's allowed models in place. Returns True if it existed."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE agent_keys SET allowed_models = ? WHERE token = ?",
                (json.dumps(list(allowed_models or [])), token),
            )
            return cur.rowcount > 0

    def delete(self, token: str) -> bool:
        """Remove a token. Returns True if it existed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM agent_keys WHERE token = ?", (token,))
            return cur.rowcount > 0

    def delete_agent(self, agent: str) -> int:
        """Remove all tokens for an agent (e.g. on undeploy). Returns count."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM agent_keys WHERE agent = ?", (agent,))
            return cur.rowcount


def default_keystore_path(data_dir: str | Path) -> Path:
    """Canonical keystore path for a controller data dir."""
    return Path(data_dir) / ".litellm_keys.db"
