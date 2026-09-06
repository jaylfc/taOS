"""peer_outbox — store-and-forward queue for DM delivery to remote contacts.

Messages queued here are drained when the peer link becomes active
(``last_seen_at`` refresh on any peer request).  Exponential backoff on
retry: 60s → 120s → 300s → 600s → 1800s cap.
"""

from __future__ import annotations

import json
import time
import uuid

from tinyagentos.base_store import BaseStore

PEER_OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS peer_outbox (
    id              TEXT PRIMARY KEY,
    contact_id      TEXT NOT NULL,
    envelope        TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   REAL NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_peer_outbox_retry ON peer_outbox(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_peer_outbox_contact ON peer_outbox(contact_id);
"""

# Exponential backoff schedule (seconds): 60, 120, 300, 600, 1800 (cap).
# Index 0 is a sentinel so ``attempts=1`` maps to ``_BACKOFF_SECONDS[1]``
# without an off-by-one adjustment.
_BACKOFF_SECONDS = (0, 60, 120, 300, 600, 1800)
_MAX_ATTEMPTS = 10  # terminal failure after 10 retries (~6 hours total)


class PeerOutboxStore(BaseStore):
    """Store-and-forward outbox for peer-to-peer message delivery.

    Each row is a serialized envelope queued for delivery to a remote
    contact.  The drain loop picks up rows where ``next_retry_at <= now``
    and attempts delivery; on failure it increments ``attempts`` and sets
    the next retry time with exponential backoff.
    """

    SCHEMA = PEER_OUTBOX_SCHEMA

    async def enqueue(
        self,
        contact_id: str,
        envelope: dict,
        *,
        next_retry_at: float | None = None,
    ) -> str:
        """Queue an envelope for delivery.  Returns the outbox row id."""
        rid = uuid.uuid4().hex[:12]
        now = time.time()
        retry_at = next_retry_at if next_retry_at is not None else now
        await self._db.execute(
            """INSERT INTO peer_outbox (id, contact_id, envelope, attempts, next_retry_at, created_at)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (rid, contact_id, json.dumps(envelope), retry_at, now),
        )
        await self._db.commit()
        return rid

    async def dequeue_due(
        self,
        contact_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Return up to *limit* rows whose ``next_retry_at`` is due, oldest first."""
        now = time.time()
        async with self._db.execute(
            """SELECT * FROM peer_outbox
               WHERE contact_id = ? AND next_retry_at <= ?
               ORDER BY next_retry_at ASC LIMIT ?""",
            (contact_id, now, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            columns = [d[0] for d in cursor.description]
        return [_row_to_dict(columns, r) for r in rows]

    async def mark_sent(self, outbox_id: str) -> bool:
        """Delete the row after successful delivery.  Returns True if deleted."""
        cursor = await self._db.execute(
            "DELETE FROM peer_outbox WHERE id = ?", (outbox_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def mark_failed(self, outbox_id: str) -> bool:
        """Increment attempts and push ``next_retry_at`` with exponential backoff.

        Returns ``True`` if the row is still pending, ``False`` if it was
        deleted (terminal failure after ``_MAX_ATTEMPTS`` retries).

        Uses ``UPDATE ... RETURNING`` for an atomic increment so concurrent
        drain passes (or overlapping retry windows) cannot undercount
        attempts.
        """
        async with self._db.execute(
            "UPDATE peer_outbox SET attempts = attempts + 1 WHERE id = ? RETURNING attempts",
            (outbox_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return False
        new_attempts = row[0]
        if new_attempts >= _MAX_ATTEMPTS:
            # Terminal failure — drop the row so it never retries again.
            await self._db.execute(
                "DELETE FROM peer_outbox WHERE id = ?", (outbox_id,)
            )
            await self._db.commit()
            return False
        delay = _BACKOFF_SECONDS[min(new_attempts, len(_BACKOFF_SECONDS) - 1)]
        await self._db.execute(
            "UPDATE peer_outbox SET next_retry_at = ? WHERE id = ?",
            (time.time() + delay, outbox_id),
        )
        await self._db.commit()
        return True

    async def purge_for_contact(self, contact_id: str) -> int:
        """Delete all outbox rows for a contact (used on revoke)."""
        cursor = await self._db.execute(
            "DELETE FROM peer_outbox WHERE contact_id = ?", (contact_id,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def count_for_contact(self, contact_id: str) -> int:
        """Return the number of pending outbox rows for a contact."""
        async with self._db.execute(
            "SELECT COUNT(*) FROM peer_outbox WHERE contact_id = ?", (contact_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _row_to_dict(columns: list[str], row) -> dict:
    if row is None:
        return {}
    return dict(zip(columns, row))
