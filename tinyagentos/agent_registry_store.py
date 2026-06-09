from __future__ import annotations

"""Agent Registry store — canonical agent-identity persistence.

Each registered agent gets a unique canonical_id minted once (immutable) and
a signed JWT-style token issued at registration time.  The signing key is an
Ed25519 keypair persisted to disk on first use (mirroring the VAPID keypair
approach in routes/desktop_browser/vapid.py).

The public key is exposed via GET /api/agents/registry/pubkey so the A2A bus
(taOSmd) can verify tokens independently without needing the private key.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id    TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL DEFAULT '',
    framework       TEXT    NOT NULL DEFAULT '',
    user_id         TEXT    NOT NULL DEFAULT '',
    origin          TEXT    NOT NULL DEFAULT 'taos-deployed',
    handle          TEXT    NOT NULL DEFAULT '',
    role            TEXT,
    capabilities    TEXT    NOT NULL DEFAULT '[]',
    created_ts      TEXT    NOT NULL,
    revoked_at      TEXT
);
"""

# ---------------------------------------------------------------------------
# Signing-key helpers (Ed25519, persisted to disk)
# ---------------------------------------------------------------------------

_REGISTRY_KEY_FILENAME = "agent_registry_signing.pem"


def load_or_create_signing_keypair(data_dir: Path) -> tuple[bytes, bytes]:
    """Return (private_key_pem_bytes, public_key_pem_bytes).

    Generates an Ed25519 keypair on first call and persists the private key
    PEM to ``<data_dir>/agent_registry_signing.pem`` with mode 0600.
    Subsequent calls load and return the same keypair.  Idempotent under
    concurrent processes — the writer uses O_EXCL so only one process
    creates the file.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
        load_pem_private_key,
    )

    data_dir.mkdir(parents=True, exist_ok=True)
    pem_path = data_dir / _REGISTRY_KEY_FILENAME

    if pem_path.exists():
        private_key = load_pem_private_key(pem_path.read_bytes(), password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        pem_bytes = private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
        try:
            fd = os.open(str(pem_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, pem_bytes)
            finally:
                os.close(fd)
        except FileExistsError:
            # Lost the race — load the winner's key instead.
            private_key = load_pem_private_key(pem_path.read_bytes(), password=None)

    private_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    public_pem = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


# ---------------------------------------------------------------------------
# Token minting (compact JWT-style — header.payload.signature, base64url)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def mint_registry_token(
    canonical_id: str,
    private_key_pem: bytes,
    *,
    user_id: str = "",
    framework: str = "",
) -> str:
    """Return a signed compact EdDSA JWT: <header>.<payload>.<signature> (base64url).

    The JWT header is exactly ``{"alg":"EdDSA","typ":"JWT"}`` so any standard
    Ed25519 JWT verifier (e.g. PyJWT, jose, or the cryptography lib directly)
    can verify it without importing tinyagentos code.

    Claims:
      sub       — canonical_id (immutable agent identity)
      iss       — "taos-registry"
      iat       — unix timestamp of issuance
      user_id   — owning user_id at registration time
      framework — agent framework at registration time

    Signed with Ed25519 over the UTF-8 bytes of ``<header_b64url>.<payload_b64url>``.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private_key = load_pem_private_key(private_key_pem, password=None)

    header = _b64url_encode(
        json.dumps({"alg": "EdDSA", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    payload = _b64url_encode(
        json.dumps(
            {
                "sub": canonical_id,
                "iss": "taos-registry",
                "iat": int(time.time()),
                "user_id": user_id,
                "framework": framework,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url_encode(private_key.sign(signing_input))
    return f"{header}.{payload}.{signature}"


def verify_registry_token(token: str, public_key_pem: bytes) -> dict:
    """Verify *token* against *public_key_pem*.

    Returns the decoded payload dict on success.
    Raises ``ValueError`` on invalid format or bad signature.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.exceptions import InvalidSignature

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("token must have three dot-separated parts")

    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig_bytes = _b64url_decode(sig_b64)

    public_key = load_pem_public_key(public_key_pem)
    try:
        public_key.verify(sig_bytes, signing_input)
    except InvalidSignature:
        raise ValueError("token signature verification failed") from None

    payload = json.loads(_b64url_decode(payload_b64))
    return payload


# ---------------------------------------------------------------------------
# Canonical-ID minting
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower().strip()).strip("-") or "agent"


def mint_canonical_id(slug: str, ts: datetime) -> str:
    """Return ``{slug}-{YYYYMMDD}-{HHMMSS}`` from *ts* (UTC)."""
    date_part = ts.strftime("%Y%m%d")
    time_part = ts.strftime("%H%M%S")
    return f"{slug}-{date_part}-{time_part}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    # Deserialise capabilities JSON list
    try:
        d["capabilities"] = json.loads(d.get("capabilities") or "[]")
    except (ValueError, TypeError):
        d["capabilities"] = []
    return d


class AgentRegistryStore(BaseStore):
    """Persistent store for the canonical agent registry.

    Keeps track of registered agents (canonical_id, signing token, metadata).
    The signing keypair lives on disk; this store only holds the data.
    """

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(
        self,
        *,
        framework: str,
        display_name: str = "",
        user_id: str = "",
        origin: str = "taos-deployed",
        handle: str = "",
        role: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
    ) -> dict:
        """Mint a canonical_id, persist the record, and return it.

        Raises ``RuntimeError`` if the store is not initialised.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised — call init() first")

        capabilities = capabilities or []
        now_utc = datetime.now(timezone.utc)
        slug = _slugify(display_name) if display_name else _slugify(framework)
        base_id = mint_canonical_id(slug, now_utc)
        canonical_id = base_id
        created_ts = now_utc.isoformat()

        # Collision guard: if the same slug+second already exists, append a
        # 2-char hex suffix to break the tie.
        suffix_n = 0
        while True:
            existing = await (
                await self._db.execute(
                    "SELECT id FROM agent_registry WHERE canonical_id = ?",
                    (canonical_id,),
                )
            ).fetchone()
            if existing is None:
                break
            suffix_n += 1
            canonical_id = f"{base_id}-{suffix_n:02x}"

        caps_json = json.dumps(capabilities)
        await self._db.execute(
            """
            INSERT INTO agent_registry
                (canonical_id, display_name, framework, user_id, origin,
                 handle, role, capabilities, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (canonical_id, display_name, framework, user_id, origin,
             handle, role, caps_json, created_ts),
        )
        await self._db.commit()

        row = await (
            await self._db.execute(
                "SELECT * FROM agent_registry WHERE canonical_id = ?",
                (canonical_id,),
            )
        ).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, canonical_id: str) -> Optional[dict]:
        """Return the record for *canonical_id*, or ``None``."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        row = await (
            await self._db.execute(
                "SELECT * FROM agent_registry WHERE canonical_id = ?",
                (canonical_id,),
            )
        ).fetchone()
        return _row_to_dict(row) if row else None

    async def list_all(self) -> list[dict]:
        """Return all registry records (revoked and active)."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        cursor = await self._db.execute("SELECT * FROM agent_registry ORDER BY id")
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------

    async def revoke(self, canonical_id: str) -> Optional[dict]:
        """Set revoked_at on *canonical_id*.  Returns updated record or None."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE agent_registry SET revoked_at = ? "
            "WHERE canonical_id = ? AND revoked_at IS NULL",
            (now, canonical_id),
        )
        await self._db.commit()
        return await self.get(canonical_id)
