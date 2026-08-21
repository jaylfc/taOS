from __future__ import annotations

"""Persistent store for cluster-worker pairing records.

Each row tracks one worker name through the pairing flow:
  announce (worker sends code_hash) →
  confirm  (admin approves, controller mints signing_key) →
  claim    (worker retrieves key once, clears confirmed flag)

A re-announce over a name that already has a signing_key is allowed: the
old key stays valid until a new confirm replaces it, so a partially-paired
worker that re-announces does not break in-flight operations.
"""

import hashlib
import hmac
import secrets
import time
import aiosqlite

from tinyagentos.base_store import BaseStore

_EXPIRY_SECS = 15 * 60  # pending entries expire after 15 minutes
_MAX_ATTEMPTS = 5        # failed code checks before the entry is invalidated

SCHEMA = """
CREATE TABLE IF NOT EXISTS cluster_pairings (
    name                TEXT NOT NULL UNIQUE,
    signing_key         BLOB,
    pending_code_hash   TEXT,
    pending_url         TEXT,
    pending_platform    TEXT,
    pending_ts          REAL,
    claim_attempts      INTEGER NOT NULL DEFAULT 0,
    confirmed           INTEGER NOT NULL DEFAULT 0,
    created_ts          REAL,
    confirmed_ts        REAL,
    blocked             INTEGER NOT NULL DEFAULT 0,
    revoked             INTEGER NOT NULL DEFAULT 0
);

-- Manual (free-tier) pairing: the admin authorises a {url, code} pair in the
-- Cluster app BEFORE the worker is known by name, so this table is keyed by the
-- code hash, not the worker name. The worker then polls manual_claim with the
-- same code; that mints the key into cluster_pairings for its name and returns
-- the admin-supplied url as the authoritative address. Single-use, short TTL.
-- Single-use is enforced by DELETE-on-claim (not a consumed flag) so a claimed
-- or expired authorisation leaves no stale signing key at rest in the table.
CREATE TABLE IF NOT EXISTS cluster_manual_pairings (
    code_hash    TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    signing_key  BLOB NOT NULL,
    created_ts   REAL NOT NULL
);
"""


def _now() -> float:
    return time.time()


class ClusterPairingStore(BaseStore):
    """SQLite-backed store for worker pairing state."""

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    async def _post_init(self) -> None:
        # `blocked` and `revoked` were added after the initial cluster_pairings
        # ship. Guarded ALTER so existing databases gain them without a
        # destructive migration (mirrors device_store.py and the
        # db_migrations.py retrofit guidance).
        if self._db is None:
            return
        cols = {
            row[1]
            for row in await (
                await self._db.execute("PRAGMA table_info(cluster_pairings)")
            ).fetchall()
        }
        if "blocked" not in cols:
            await self._db.execute(
                "ALTER TABLE cluster_pairings ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0"
            )
        if "revoked" not in cols:
            await self._db.execute(
                "ALTER TABLE cluster_pairings ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0"
            )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def announce(
        self,
        name: str,
        url: str,
        platform: str,
        code_hash: str,
    ) -> None:
        """Upsert a pending pairing announcement.

        Never touches signing_key — the existing paired key for a re-announcing
        worker stays valid until the new pairing is confirmed.
        Resets attempt counter and confirmed flag so the new flow starts clean.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        ts = _now()
        await self._db.execute(
            """
            INSERT INTO cluster_pairings
                (name, pending_code_hash, pending_url, pending_platform,
                 pending_ts, claim_attempts, confirmed, created_ts)
            VALUES (?, ?, ?, ?, ?, 0, 0, ?)
            ON CONFLICT(name) DO UPDATE SET
                pending_code_hash = excluded.pending_code_hash,
                pending_url       = excluded.pending_url,
                pending_platform  = excluded.pending_platform,
                pending_ts        = excluded.pending_ts,
                claim_attempts    = 0,
                confirmed         = 0
            """,
            (name, code_hash, url, platform, ts, ts),
        )
        await self._db.commit()

    async def confirm(self, name: str, code: str) -> bool:
        """Verify the code, mint a signing key, and mark confirmed=1.

        Returns True on success, False if the entry is absent, expired,
        at max attempts, the code is wrong, or the worker is currently
        blocked (an admin must unblock before a new key is issued).
        Increments attempt counter on a wrong code.

        On success the blocked/revoked flags are cleared: re-confirming
        issues a fresh signing key, restoring the node's auth.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        row = await self._fetch_row(name)
        if row is None:
            return False
        if row["blocked"]:
            return False
        if not self._pending_valid(row):
            return False
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        if not hmac.compare_digest(code_hash, row["pending_code_hash"] or ""):
            await self._increment_attempts(name)
            return False
        key = secrets.token_bytes(32)
        ts = _now()
        await self._db.execute(
            """
            UPDATE cluster_pairings
               SET signing_key   = ?,
                   confirmed     = 1,
                   blocked       = 0,
                   revoked       = 0,
                   confirmed_ts  = ?
             WHERE name = ?
            """,
            (key, ts, name),
        )
        await self._db.commit()
        return True

    async def claim(self, name: str, code: str) -> bytes | None:
        """Return the signing key exactly once after a successful confirm.

        Pre-confirm: returns None (caller should treat as 202 awaiting_confirm).
        Post-confirm, correct code: returns the key and clears confirmed/pending.
        Wrong code: increments attempts, returns None.
        Unknown/invalidated name: returns None.

        The clear is gated on confirmed=1 so two concurrent callers cannot
        both receive the key: only the caller whose UPDATE flips confirmed
        1->0 (rowcount==1) wins; the other gets None.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        row = await self._fetch_row(name)
        if row is None:
            return None
        if not row["confirmed"]:
            # Not confirmed yet -- not an error, just not ready.
            return None
        if not self._pending_valid(row):
            # Expired even though confirmed -- worker must re-announce.
            return None
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        if not hmac.compare_digest(code_hash, row["pending_code_hash"] or ""):
            await self._increment_attempts(name)
            return None
        key = bytes(row["signing_key"])
        # Conditional clear: WHERE confirmed = 1 ensures only one concurrent
        # caller can win.  The winner gets rowcount == 1; the loser gets 0.
        cursor = await self._db.execute(
            """
            UPDATE cluster_pairings
               SET pending_code_hash = NULL,
                   pending_url       = NULL,
                   pending_platform  = NULL,
                   pending_ts        = NULL,
                   claim_attempts    = 0,
                   confirmed         = 0
             WHERE name = ?
               AND confirmed = 1
            """,
            (name,),
        )
        await self._db.commit()
        if cursor.rowcount != 1:
            return None
        return key

    async def manual_authorize(self, url: str, code: str) -> None:
        """Authorise a manual (free-tier) pairing: store the admin-supplied url
        plus a freshly minted signing key, keyed by the code hash. The worker is
        not known by name yet. A re-authorise for the same code replaces the
        prior record and its key."""
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        key = secrets.token_bytes(32)
        await self._db.execute(
            """
            INSERT INTO cluster_manual_pairings
                (code_hash, url, signing_key, created_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code_hash) DO UPDATE SET
                url         = excluded.url,
                signing_key = excluded.signing_key,
                created_ts  = excluded.created_ts
            """,
            (code_hash, url, key, _now()),
        )
        await self._db.commit()

    async def manual_claim(self, name: str, code: str) -> tuple[bytes, str] | None:
        """Claim a manual authorisation. If a non-expired record exists for this
        code, persist its signing key for `name` in cluster_pairings, delete the
        manual record (single-use, so no stale key is left at rest), and return
        (key, url). Returns None when no authorisation matches yet, so the worker
        keeps polling until the admin enters the IP and code. An expired record
        is deleted on access so abandoned authorisations do not accumulate."""
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        cursor = await self._db.execute(
            "SELECT url, signing_key, created_ts"
            " FROM cluster_manual_pairings WHERE code_hash = ?",
            (code_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if (_now() - row["created_ts"]) > _EXPIRY_SECS:
            # Clean up the abandoned authorisation instead of leaving the key at rest.
            await self._db.execute(
                "DELETE FROM cluster_manual_pairings WHERE code_hash = ?",
                (code_hash,),
            )
            await self._db.commit()
            return None
        key = bytes(row["signing_key"])
        url = row["url"]
        # A blocked worker may not claim a new key until an admin unblocks it.
        # (revoked-only is fine: re-pairing restores the node.)
        existing = await self._fetch_row(name)
        if existing is not None and existing["blocked"]:
            return None
        # Single-use: DELETE is the race guard. Only the caller whose DELETE
        # actually removes the row (rowcount == 1) wins; a concurrent caller
        # gets rowcount == 0 and None. The row is gone afterwards, so no
        # consumed signing key lingers in the table.
        consume = await self._db.execute(
            "DELETE FROM cluster_manual_pairings WHERE code_hash = ?",
            (code_hash,),
        )
        if consume.rowcount != 1:
            await self._db.commit()
            return None
        # Persist the key under the worker name so its signed requests authenticate.
        # Clear blocked/revoked: a successful claim restores the node's auth.
        await self._db.execute(
            """
            INSERT INTO cluster_pairings (name, signing_key, created_ts, confirmed)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(name) DO UPDATE SET
                signing_key = excluded.signing_key,
                blocked     = 0,
                revoked     = 0
            """,
            (name, key, _now()),
        )
        await self._db.commit()
        return key, url

    async def get_signing_key(self, name: str) -> bytes | None:
        """Return the worker's current signing key, or None if not paired,
        revoked, or blocked.

        A revoked or blocked node has its signing key treated as dead: the
        HMAC gate in require_worker_hmac will 401 it. This mirrors
        DeviceStore.get_by_token's ``revoked = 0 AND blocked = 0`` guard.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        row = await self._fetch_row(name)
        if row is None or row["signing_key"] is None:
            return None
        if row["revoked"] or row["blocked"]:
            return None
        return bytes(row["signing_key"])

    async def record_failed_attempt(self, name: str) -> None:
        """Increment the failed-attempt counter for a worker.

        Used by the HMAC gate when a signature check fails — not the same as
        a pairing code check, but shares the same counter so the entry gets
        invalidated after _MAX_ATTEMPTS total failures.
        """
        await self._increment_attempts(name)

    # ------------------------------------------------------------------
    # Node revocation / blocking (mirrors DeviceStore semantics)
    # ------------------------------------------------------------------

    async def revoke(self, name: str) -> bool:
        """Revoke a node: invalidate its signing key so HMAC auth fails, but
        allow re-pairing (a subsequent confirm/claim issues a fresh key).

        Returns True if the row was updated, False if the worker was not found
        or was already revoked.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        cur = await self._db.execute(
            "UPDATE cluster_pairings SET revoked = 1 "
            "WHERE name = ? AND revoked = 0",
            (name,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def block(self, name: str) -> bool:
        """Block a node: revoke its signing key AND prevent re-pairing.

        A blocked node cannot authenticate (get_signing_key returns None) and
        cannot re-pair (confirm/manual_claim refuse while blocked). The admin
        must unblock before the node can obtain a fresh signing key.

        Returns True if the row was updated, False if not found or already
        blocked.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        cur = await self._db.execute(
            "UPDATE cluster_pairings SET revoked = 1, blocked = 1 "
            "WHERE name = ? AND blocked = 0",
            (name,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def unblock(self, name: str) -> bool:
        """Clear the blocked flag. The old signing key stays dead (revoked=1)
        so the node must re-pair via the normal announce/confirm/claim flow to
        obtain a fresh key."""
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        cur = await self._db.execute(
            "UPDATE cluster_pairings SET blocked = 0 "
            "WHERE name = ? AND blocked = 1",
            (name,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def pairing_state(self, name: str) -> dict | None:
        """Return a summary dict for the route layer; None when the row is absent.

        Keys: has_pending, confirmed, expired, attempts_capped, revoked, blocked.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        row = await self._fetch_row(name)
        if row is None:
            return None
        has_pending = row["pending_code_hash"] is not None
        confirmed = bool(row["confirmed"])
        ts = row["pending_ts"]
        expired = has_pending and (ts is None or (_now() - ts) > _EXPIRY_SECS)
        attempts_capped = row["claim_attempts"] >= _MAX_ATTEMPTS
        return {
            "has_pending": has_pending,
            "confirmed": confirmed,
            "expired": expired,
            "attempts_capped": attempts_capped,
            "revoked": bool(row["revoked"]),
            "blocked": bool(row["blocked"]),
        }



    async def list_pending(self) -> list[dict]:
        """Return all rows that have a non-expired pending announcement."""
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        min_ts = _now() - _EXPIRY_SECS
        cursor = await self._db.execute(
            """
            SELECT name, pending_url, pending_platform, pending_ts
              FROM cluster_pairings
             WHERE pending_code_hash IS NOT NULL
               AND confirmed = 0
               AND claim_attempts < ?
               AND pending_ts > ?
             ORDER BY pending_ts
            """,
            (_MAX_ATTEMPTS, min_ts),
        )
        rows = await cursor.fetchall()
        return [
            {
                "name": r["name"],
                "url": r["pending_url"],
                "platform": r["pending_platform"],
                "announced_at": r["pending_ts"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetch_row(self, name: str) -> aiosqlite.Row | None:
        cursor = await self._db.execute(
            "SELECT * FROM cluster_pairings WHERE name = ?", (name,)
        )
        return await cursor.fetchone()

    def _pending_valid(self, row: aiosqlite.Row) -> bool:
        """Return True if the pending entry is not expired and under attempt cap."""
        if row["pending_code_hash"] is None:
            return False
        if row["claim_attempts"] >= _MAX_ATTEMPTS:
            return False
        ts = row["pending_ts"]
        if ts is None or (_now() - ts) > _EXPIRY_SECS:
            return False
        return True

    async def _increment_attempts(self, name: str) -> None:
        await self._db.execute(
            "UPDATE cluster_pairings SET claim_attempts = claim_attempts + 1 WHERE name = ?",
            (name,),
        )
        await self._db.commit()
