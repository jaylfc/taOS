from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

from tinyagentos.base_store import BaseStore


CONTACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id        TEXT PRIMARY KEY,       -- "hub:{username}" e.g. "hub:hogne"
    hub_username      TEXT NOT NULL UNIQUE,
    display_name      TEXT NOT NULL,
    ed25519_pub       TEXT NOT NULL,          -- pinned at friend-accept
    x25519_pub        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',  -- pending|active|blocked|revoked
    local_crm_id      TEXT,                   -- optional link to existing CRM row
    created_at        REAL NOT NULL,
    revoked_at        REAL
);

CREATE TABLE IF NOT EXISTS peer_links (
    contact_id              TEXT PRIMARY KEY REFERENCES contacts(contact_id),
    inbound_token_hash      TEXT NOT NULL,    -- token WE minted for their instance (SHA-256); indexed for lookup
    outbound_token          TEXT NOT NULL,    -- token THEY minted for us (stored in plaintext — must be presented on outbound requests; at-rest encryption deferred to post-MVP)
    endpoints               TEXT NOT NULL DEFAULT '[]',  -- JSON list of advertised endpoints
    established_at          REAL NOT NULL,
    last_seen_at            REAL,
    revoked_at              REAL
);

CREATE INDEX IF NOT EXISTS idx_peer_links_token_hash ON peer_links(inbound_token_hash);

CREATE TABLE IF NOT EXISTS peer_nonces (
    nonce                   TEXT NOT NULL,       -- 16-byte hex nonce from envelope
    contact_id              TEXT NOT NULL,
    kind                    TEXT NOT NULL DEFAULT '',
    seen_at                 REAL NOT NULL,
    PRIMARY KEY (contact_id, kind, nonce)
);
"""

# Peer tokens are 256-bit opaque strings, SHA-256 hashed at rest (same pattern
# as registry tokens).  The peer-token sub concept is "contact:{hub_username}".
_PEER_TOKEN_BYTES = 32
# Nonces live for the envelope freshness window (300s) + 30s clock-skew grace
# + 60s buffer.  After this window a nonce is pruned so the table stays small.
NONCE_MAX_AGE_SECS = 600

VALID_CONTACT_STATUSES = frozenset({"pending", "active", "blocked", "revoked"})


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_peer_token() -> str:
    """Return a new opaque peer token (hex-encoded random bytes)."""
    return secrets.token_hex(_PEER_TOKEN_BYTES)


class ContactsStore(BaseStore):
    """SQLite store for contacts and peer links.

    Contacts are created from accepted hub friend-edges. Each active contact
    gets a peer link with bidirectional tokens for the instance-to-instance
    peer channel (``POST /api/peer/*``).
    """

    SCHEMA = CONTACTS_SCHEMA

    MIGRATIONS: list = []

    # ------------------------------------------------------------------
    # contacts
    # ------------------------------------------------------------------

    async def add_contact(
        self,
        *,
        contact_id: str,
        hub_username: str,
        display_name: str,
        ed25519_pub: str,
        x25519_pub: str,
        status: str = "active",
        local_crm_id: str | None = None,
    ) -> None:
        """Insert or upsert (by contact_id) a contact row.

        On conflict (same contact_id) the key material is updated — this is
        the trust-on-first-use refresh when a friend re-accepts.
        """
        if status not in VALID_CONTACT_STATUSES:
            raise ValueError(
                f"invalid contact status: {status!r} — "
                f"must be one of {sorted(VALID_CONTACT_STATUSES)}"
            )
        now = time.time()
        await self._db.execute(
            """INSERT INTO contacts
               (contact_id, hub_username, display_name, ed25519_pub, x25519_pub,
                status, local_crm_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(contact_id) DO UPDATE SET
                 ed25519_pub = excluded.ed25519_pub,
                 x25519_pub = excluded.x25519_pub,
                 display_name = excluded.display_name,
                 status = excluded.status,
                 local_crm_id = excluded.local_crm_id,
                 revoked_at = NULL""",
            (contact_id, hub_username, display_name, ed25519_pub, x25519_pub,
             status, local_crm_id, now),
        )
        await self._db.commit()

    async def get_contact(self, contact_id: str) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM contacts WHERE contact_id = ?", (contact_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
        return _row_to_dict(columns, rows[0]) if rows else None

    async def get_contact_by_username(self, hub_username: str) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM contacts WHERE hub_username = ?", (hub_username,)
        ) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
        return _row_to_dict(columns, rows[0]) if rows else None

    async def set_contact_status(self, contact_id: str, status: str) -> None:
        if status not in VALID_CONTACT_STATUSES:
            raise ValueError(
                f"invalid contact status: {status!r} — "
                f"must be one of {sorted(VALID_CONTACT_STATUSES)}"
            )
        now = time.time()
        if status in ("blocked", "revoked"):
            await self._db.execute(
                "UPDATE contacts SET status = ?, revoked_at = ? WHERE contact_id = ?",
                (status, now, contact_id),
            )
        else:
            await self._db.execute(
                "UPDATE contacts SET status = ?, revoked_at = NULL WHERE contact_id = ?",
                (status, contact_id),
            )
        await self._db.commit()

    async def list_contacts(
        self, status: str | None = None
    ) -> list[dict]:
        """List contacts, optionally filtered by status."""
        if status:
            async with self._db.execute(
                "SELECT * FROM contacts WHERE status = ? ORDER BY created_at",
                (status,),
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
        else:
            async with self._db.execute(
                "SELECT * FROM contacts ORDER BY created_at"
            ) as cursor:
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
        return [_row_to_dict(columns, r) for r in rows]

    # ------------------------------------------------------------------
    # peer_links
    # ------------------------------------------------------------------

    async def establish_peer_link(
        self,
        *,
        contact_id: str,
        inbound_token: str,
        outbound_token: str,
        endpoints: list[str] | None = None,
    ) -> None:
        """Create a peer link row.  Called after the handshake completes.

        ``inbound_token`` is the plaintext token WE minted for them (hashed at
        rest); ``outbound_token`` is the token THEY minted for us.
        """
        now = time.time()
        eps_json = _json_dumps(endpoints or [])
        await self._db.execute(
            """INSERT INTO peer_links
               (contact_id, inbound_token_hash, outbound_token, endpoints,
                established_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(contact_id) DO UPDATE SET
                 inbound_token_hash = excluded.inbound_token_hash,
                 outbound_token = excluded.outbound_token,
                 endpoints = excluded.endpoints,
                 established_at = excluded.established_at,
                 revoked_at = NULL""",
            (contact_id, _hash_token(inbound_token), outbound_token, eps_json, now),
        )
        await self._db.commit()

    async def get_peer_link(self, contact_id: str) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM peer_links WHERE contact_id = ?", (contact_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
        if not rows:
            return None
        rec = _row_to_dict(columns, rows[0])
        # Deserialise endpoints for the caller.
        rec["endpoints"] = _json_loads(rec.get("endpoints", "[]"))
        return rec

    async def find_contact_by_inbound_token(self, token: str) -> Optional[dict]:
        """Look up a contact by their inbound token (the one we minted for them).

        Returns the contact + peer_link joined row, or None if the token is
        invalid, the contact is not active, or the link is revoked.
        """
        token_hash = _hash_token(token)
        async with self._db.execute(
            """SELECT c.*, p.inbound_token_hash, p.outbound_token, p.endpoints,
                      p.established_at, p.last_seen_at, p.revoked_at as link_revoked_at
               FROM contacts c
               JOIN peer_links p ON c.contact_id = p.contact_id
               WHERE p.inbound_token_hash = ?
                 AND c.status = 'active'
                 AND p.revoked_at IS NULL""",
            (token_hash,),
        ) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
        if not rows:
            return None
        rec = _row_to_dict(columns, rows[0])
        rec["endpoints"] = _json_loads(rec.get("endpoints", "[]"))
        return rec

    async def record_nonce(self, nonce: str, contact_id: str, kind: str = "") -> bool:
        """Record a seen nonce for replay protection.

        Returns True if the nonce was recorded (first time seen), False if it
        was already present (replay detected).

        Nonces older than ``NONCE_MAX_AGE_SECS`` are pruned on insert so the
        table does not grow unbounded.

        The uniqueness key is ``(contact_id, kind, nonce)`` — a nonce is only
        a replay when all three match (per design section 2).  A rejected
        envelope (bad sig / bad to) must NOT burn its nonce; the caller should
        only record a nonce AFTER verification passes.
        """
        now = time.time()
        # Prune expired nonces (opportunistic, one DELETE per insert keeps the
        # table small without a separate vacuum job).  Commit the prune in its
        # own transaction so a replay (IntegrityError) does not roll it back.
        cutoff = now - NONCE_MAX_AGE_SECS
        await self._db.execute(
            "DELETE FROM peer_nonces WHERE seen_at < ?", (cutoff,)
        )
        await self._db.commit()
        try:
            await self._db.execute(
                "INSERT INTO peer_nonces (nonce, contact_id, kind, seen_at) VALUES (?, ?, ?, ?)",
                (nonce, contact_id, kind, now),
            )
            await self._db.commit()
            return True
        except sqlite3.IntegrityError:
            # Nonce already exists for this (contact_id, kind) — replay detected.
            await self._db.rollback()
            return False

    async def mark_peer_seen(self, contact_id: str) -> None:
        """Update last_seen_at for the peer link (called on every valid request)."""
        await self._db.execute(
            "UPDATE peer_links SET last_seen_at = ? WHERE contact_id = ?",
            (time.time(), contact_id),
        )
        await self._db.commit()

    async def revoke_peer_link(self, contact_id: str) -> None:
        """Revoke the peer link (cascades to block contact)."""
        now = time.time()
        await self._db.execute(
            "UPDATE peer_links SET revoked_at = ? WHERE contact_id = ?",
            (now, contact_id),
        )
        await self._db.execute(
            "UPDATE contacts SET status = 'revoked', revoked_at = ? WHERE contact_id = ?",
            (now, contact_id),
        )
        await self._db.commit()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _row_to_dict(columns: list[str], row) -> dict:
    """Convert a result row to a dict using pre-captured column names."""
    if row is None:
        return {}
    return dict(zip(columns, row))


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj)


def _json_loads(s: str):
    import json
    return json.loads(s)
