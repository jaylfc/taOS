from __future__ import annotations

import base64
import hashlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

from tinyagentos.base_store import BaseStore

SECRETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'general',
    value TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS secret_access (
    secret_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    PRIMARY KEY (secret_id, agent_name),
    FOREIGN KEY (secret_id) REFERENCES secrets(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS secret_categories (
    name TEXT PRIMARY KEY,
    description TEXT DEFAULT ''
);
"""

DEFAULT_CATEGORIES = ["api-keys", "tokens", "credentials", "webhooks", "general"]

# Prefix written into every Fernet-encrypted value so decrypt can distinguish
# new (Fernet) from old (XOR) ciphertext and migrate transparently.
_FERNET_PREFIX = "fernet:"


def _xor_key() -> bytes:
    """Derive the legacy XOR key from /etc/machine-id (or a fixed fallback)."""
    machine_id_path = Path("/etc/machine-id")
    machine_id = (
        machine_id_path.read_text().strip()
        if machine_id_path.exists()
        else "tinyagentos-default"
    )
    return hashlib.sha256(machine_id.encode()).digest()


def _xor_decrypt(encrypted: str) -> str:
    """Decrypt an XOR-obfuscated ciphertext (legacy format)."""
    key = _xor_key()
    data = base64.b64decode(encrypted)
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode()


# Per-process Fernet key cache keyed by key_dir path string.
_fernet_key_cache: dict[str, bytes] = {}


def _get_fernet_key(key_dir: Path) -> bytes:
    """Return the 32-byte encryption key for *key_dir*, generating on first use.

    Stored at ``<key_dir>/.secrets_key`` with mode 0600.  The file holds raw
    bytes (not base64) so a stripped text editor cannot accidentally corrupt it.
    """
    cache_key = str(key_dir)
    if cache_key in _fernet_key_cache:
        return _fernet_key_cache[cache_key]

    key_path = Path(key_dir) / ".secrets_key"
    if key_path.exists():
        raw = key_path.read_bytes()
        if len(raw) != 32:
            raise ValueError(
                f"Corrupt Fernet key file at {key_path}: expected 32 bytes, got "
                f"{len(raw)}. Remove the file only if you have no secrets to recover, "
                "then restart to generate a fresh key."
            )
        _fernet_key_cache[cache_key] = raw
        return raw

    # Generate a fresh random 32-byte key only when the file does not exist.
    raw = os.urandom(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = key_path.with_suffix(".tmp")
    tmp.write_bytes(raw)
    os.chmod(tmp, 0o600)
    tmp.rename(key_path)
    _fernet_key_cache[cache_key] = raw
    return raw


def _fernet_token(key_dir: Path) -> "Fernet":
    """Return a :class:`cryptography.fernet.Fernet` instance for *key_dir*."""
    from cryptography.fernet import Fernet
    raw = _get_fernet_key(key_dir)
    # Fernet requires a 32-byte URL-safe base64-encoded key.
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt(value: str, key_dir: Path | None = None) -> str:
    """Encrypt *value* with Fernet when *key_dir* is provided.

    Falls back to the legacy XOR obfuscation when *key_dir* is None so the
    module stays importable in test contexts that don't supply a data dir.
    The returned string is prefixed with ``_FERNET_PREFIX`` when using Fernet
    so :func:`_decrypt` can detect the format.
    """
    if key_dir is None:
        # Legacy path — only reached in tests / callers that don't provide a dir.
        key = _xor_key()
        data = value.encode()
        encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return base64.b64encode(encrypted).decode()
    token = _fernet_token(key_dir).encrypt(value.encode()).decode()
    return _FERNET_PREFIX + token


def _decrypt(encrypted: str, key_dir: Path | None = None) -> str:
    """Decrypt *encrypted* transparently handling both Fernet and XOR formats.

    Migration path: if *encrypted* does NOT start with ``_FERNET_PREFIX`` it is
    an old XOR ciphertext.  We decrypt with XOR and, when *key_dir* is provided,
    immediately re-encrypt with Fernet and return the plaintext — the caller
    (``SecretsStore``) is responsible for persisting the upgraded ciphertext.
    """
    if encrypted.startswith(_FERNET_PREFIX):
        if key_dir is None:
            raise ValueError("Fernet-encrypted secret requires key_dir to decrypt")
        token = encrypted[len(_FERNET_PREFIX):]
        return _fernet_token(key_dir).decrypt(token.encode()).decode()
    # Legacy XOR ciphertext.
    return _xor_decrypt(encrypted)


class SecretsStore(BaseStore):
    SCHEMA = SECRETS_SCHEMA

    def __init__(self, db_path: Path):
        super().__init__(db_path)
        # key_dir is the directory that holds .secrets_key (same as data_dir).
        self._key_dir: Path = Path(db_path).parent

    async def _post_init(self) -> None:
        await self._db.execute("PRAGMA foreign_keys = ON")
        # Seed default categories
        for cat in DEFAULT_CATEGORIES:
            await self._db.execute(
                "INSERT OR IGNORE INTO secret_categories (name) VALUES (?)", (cat,)
            )
        await self._db.commit()

    def _enc(self, value: str) -> str:
        return _encrypt(value, self._key_dir)

    async def _dec(self, ciphertext: str, *, name: str = "", secret_id: int | None = None) -> str:
        """Decrypt *ciphertext*, migrating XOR→Fernet transparently.

        When an old XOR ciphertext is detected we decrypt it with the legacy
        key, immediately re-encrypt with Fernet, and persist the upgraded value
        so the migration is a one-time write per secret.
        """
        if ciphertext.startswith(_FERNET_PREFIX):
            return _decrypt(ciphertext, self._key_dir)
        # Legacy XOR — decrypt and re-encrypt with Fernet.
        plaintext = _decrypt(ciphertext, None)
        upgraded = _encrypt(plaintext, self._key_dir)
        if secret_id is not None:
            now = int(time.time())
            await self._db.execute(
                "UPDATE secrets SET value = ?, updated_at = ? WHERE id = ?",
                (upgraded, now, secret_id),
            )
            await self._db.commit()
        return plaintext

    async def add(
        self,
        name: str,
        value: str,
        category: str = "general",
        description: str = "",
        agents: list[str] | None = None,
    ) -> int:
        now = int(time.time())
        encrypted = self._enc(value)
        cursor = await self._db.execute(
            "INSERT INTO secrets (name, category, value, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, category, encrypted, description, now, now),
        )
        secret_id = cursor.lastrowid
        if agents:
            for agent in agents:
                await self._db.execute(
                    "INSERT INTO secret_access (secret_id, agent_name) VALUES (?, ?)",
                    (secret_id, agent),
                )
        await self._db.commit()
        return secret_id

    async def get(self, name: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, name, category, value, description, created_at, updated_at FROM secrets WHERE name = ?",
            (name,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        agents = await self._get_agents(row[0])
        return {
            "id": row[0],
            "name": row[1],
            "category": row[2],
            "value": await self._dec(row[3], name=row[1], secret_id=row[0]),
            "description": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "agents": agents,
        }

    async def list(self, category: str | None = None) -> list[dict]:
        sql = "SELECT id, name, category, description, created_at, updated_at FROM secrets"
        params: list = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY category, name"
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        results = []
        for r in rows:
            agents = await self._get_agents(r[0])
            results.append(
                {
                    "id": r[0],
                    "name": r[1],
                    "category": r[2],
                    "description": r[3],
                    "created_at": r[4],
                    "updated_at": r[5],
                    "agents": agents,
                }
            )
        return results

    async def update(
        self,
        name: str,
        value: str | None = None,
        category: str | None = None,
        description: str | None = None,
        agents: list[str] | None = None,
    ) -> bool:
        secret = await self.get(name)
        if not secret:
            return False
        now = int(time.time())
        if value is not None:
            await self._db.execute(
                "UPDATE secrets SET value = ?, updated_at = ? WHERE name = ?",
                (self._enc(value), now, name),
            )
        if category is not None:
            await self._db.execute(
                "UPDATE secrets SET category = ?, updated_at = ? WHERE name = ?",
                (category, now, name),
            )
        if description is not None:
            await self._db.execute(
                "UPDATE secrets SET description = ?, updated_at = ? WHERE name = ?",
                (description, now, name),
            )
        if agents is not None:
            await self._db.execute(
                "DELETE FROM secret_access WHERE secret_id = ?", (secret["id"],)
            )
            for agent in agents:
                await self._db.execute(
                    "INSERT INTO secret_access (secret_id, agent_name) VALUES (?, ?)",
                    (secret["id"], agent),
                )
        await self._db.commit()
        return True

    async def delete(self, name: str) -> bool:
        cursor = await self._db.execute("DELETE FROM secrets WHERE name = ?", (name,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_agent_secrets(self, agent_name: str) -> list[dict]:
        """Get all secrets accessible to a specific agent."""
        async with self._db.execute(
            """
            SELECT s.id, s.name, s.category, s.value, s.description
            FROM secrets s
            JOIN secret_access sa ON sa.secret_id = s.id
            WHERE sa.agent_name = ?
            ORDER BY s.category, s.name
            """,
            (agent_name,),
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "name": r[1],
                "category": r[2],
                "value": await self._dec(r[3], name=r[1], secret_id=r[0]),
                "description": r[4],
            })
        return result

    async def get_categories(self) -> list[dict]:
        async with self._db.execute(
            "SELECT name, description FROM secret_categories ORDER BY name"
        ) as cursor:
            return [{"name": r[0], "description": r[1]} for r in await cursor.fetchall()]

    async def _get_agents(self, secret_id: int) -> list[str]:
        async with self._db.execute(
            "SELECT agent_name FROM secret_access WHERE secret_id = ?", (secret_id,)
        ) as cursor:
            return [r[0] for r in await cursor.fetchall()]
