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
    confirmed_ts        REAL
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
        at max attempts, or the code is wrong.
        Increments attempt counter on a wrong code.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        row = await self._fetch_row(name)
        if row is None:
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

        Callers must distinguish "not confirmed yet" from "confirmed but wrong
        code" by checking list_pending or by the attempt count. The route
        layer handles the HTTP status differentiation.
        """
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        row = await self._fetch_row(name)
        if row is None:
            return None
        if not row["confirmed"]:
            # Not confirmed yet — not an error, just not ready.
            return None
        if not self._pending_valid(row):
            # Expired even though confirmed — worker must re-announce.
            return None
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        if not hmac.compare_digest(code_hash, row["pending_code_hash"] or ""):
            await self._increment_attempts(name)
            return None
        key = bytes(row["signing_key"])
        # Clear pending fields and confirmed flag (key delivered once).
        await self._db.execute(
            """
            UPDATE cluster_pairings
               SET pending_code_hash = NULL,
                   pending_url       = NULL,
                   pending_platform  = NULL,
                   pending_ts        = NULL,
                   claim_attempts    = 0,
                   confirmed         = 0
             WHERE name = ?
            """,
            (name,),
        )
        await self._db.commit()
        return key

    async def get_signing_key(self, name: str) -> bytes | None:
        """Return the worker's current signing key, or None if not paired."""
        if self._db is None:
            raise RuntimeError("ClusterPairingStore not initialised")
        row = await self._fetch_row(name)
        if row is None or row["signing_key"] is None:
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
    # Read
    # ------------------------------------------------------------------

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
               AND pending_ts > ?
             ORDER BY pending_ts
            """,
            (min_ts,),
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
