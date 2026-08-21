from __future__ import annotations

"""Store for device pairing (consent) requests.

Each record tracks one inbound pairing request from an external device/app.
Pending requests wait for the instance user to approve or deny (surfaced
through a device_pairing Decision). Accepted requests carry the minted
``device_id`` so the caller can poll and retrieve the issued device token.

The state machine is: pending -> accepted | denied | expired (terminal).
``set_decision`` is atomic -- it uses a conditional UPDATE that only matches
rows still in ``pending`` status, so two concurrent approve/deny calls cannot
both win a read-check-then-write race.

The ``verify_code`` is a human-comparison nonce (F3): it is persisted only so
the Decision text can display it for the approving user, and is NEVER returned
by ``get``/poll -- the route layer strips it. It is never server-checked and no
endpoint accepts it as input.

Concurrency scope: the pending-cap check-then-insert is serialized by an
in-process lock (``_create_lock``), which is sound because taOS serves from a
single process (``uvicorn.run`` with an app object -- no worker forking). If
multi-process serving is ever introduced, the cap check must move into a
single transactional INSERT ... WHERE (SELECT COUNT ...) statement.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
import asyncio

from tinyagentos.base_store import BaseStore

# Mirror the external-agent request cap (see routes/agent_auth_requests.py).
_PENDING_CAP = 5
# ~10 min expiry for a pending pairing request (F4).
_TTL_SECS = 600


SCHEMA = """
CREATE TABLE IF NOT EXISTS device_pair_requests (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    verify_code     TEXT NOT NULL,
    requester_ip    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    device_id       TEXT,
    token_claimed   INTEGER NOT NULL DEFAULT 0,
    created_ts      TEXT NOT NULL,
    expires_at_ts   TEXT NOT NULL,
    decided_ts      TEXT,
    decided_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_pair_requests_status ON device_pair_requests(status);
CREATE INDEX IF NOT EXISTS idx_pair_requests_expires ON device_pair_requests(status, expires_at_ts);
"""

_VALID_DECISION_STATUSES = frozenset({"accepted", "denied", "expired"})

# Columns the route is allowed to surface (verify_code is deliberately excluded
# from every read path other than the creation response -- see F3 / criterion 5).
_SAFE_COLS = (
    "id, platform, display_name, requester_ip, status, device_id, "
    "token_claimed, created_ts, expires_at_ts, decided_ts, decided_by"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _is_expired(expires_at_ts: Optional[str], now: Optional[datetime] = None) -> bool:
    """True when the pending request's expiry has passed (F6: enforce at approve
    time, not only at poll). Returns False for records with no expiry."""
    if not expires_at_ts:
        return False
    try:
        exp = datetime.fromisoformat(expires_at_ts)
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    now = now or _now()
    return exp <= now


def _live_status(record: dict) -> str:
    """Resolve the status a reader sees: a pending request past its TTL is
    reported as 'expired' without mutating it (the decide-time path persists it)."""
    status = record.get("status")
    if status == "pending" and _is_expired(record.get("expires_at_ts")):
        return "expired"
    return status


class DevicePairRequestsStore(BaseStore):
    """Persistent store for device pairing (consent) requests."""

    SCHEMA = SCHEMA

    def __init__(self, db_path):
        super().__init__(db_path)
        self._create_lock = asyncio.Lock()

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        platform: str,
        display_name: str = "",
        verify_code: str = "",
        requester_ip: str = "",
    ) -> dict:
        """Create a new pending pair request. Returns the stored record via
        ``get``, i.e. WITHOUT ``verify_code`` -- callers that need the code
        already hold it (they supplied it)."""
        if self._db is None:
            raise RuntimeError("DevicePairRequestsStore not initialised -- call init() first")

        pair_request_id = uuid.uuid4().hex
        now = _now()
        expires_at = _iso(now + timedelta(seconds=_TTL_SECS))

        await self._db.execute(
            """
            INSERT INTO device_pair_requests
                (id, platform, display_name, verify_code, requester_ip,
                 status, token_claimed, created_ts, expires_at_ts)
            VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (pair_request_id, platform, display_name, verify_code, requester_ip,
             _iso(now), expires_at),
        )
        await self._db.commit()
        return await self.get(pair_request_id)

    async def set_decision(
        self,
        pair_request_id: str,
        status: str,
        *,
        device_id: Optional[str] = None,
        decided_by: str,
    ) -> Optional[dict]:
        """Atomically transition a pending request to accepted / denied / expired.

        Returns the updated record on success, or ``None`` if the row was
        already decided (rowcount == 0 from the conditional UPDATE).
        Raises ``ValueError`` for an invalid target status.
        """
        if self._db is None:
            raise RuntimeError("DevicePairRequestsStore not initialised")
        if status not in _VALID_DECISION_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_VALID_DECISION_STATUSES)}, got {status!r}"
            )

        now_iso = _iso(_now())
        cur = await self._db.execute(
            """
            UPDATE device_pair_requests
               SET status       = ?,
                   device_id    = ?,
                   decided_ts   = ?,
                   decided_by   = ?
             WHERE id = ? AND status = 'pending'
            """,
            (status, device_id if status == "accepted" else None,
             now_iso, decided_by, pair_request_id),
        )
        await self._db.commit()
        if cur.rowcount == 0:
            return None
        return await self.get(pair_request_id)

    async def claim_scoped_token(self, pair_request_id: str) -> bool:
        """Atomically claim the one-time token release for an accepted request.

        Returns True if this caller is the one that claimed it (so the route may
        release the scoped_token), False if it was already claimed.
        """
        if self._db is None:
            raise RuntimeError("DevicePairRequestsStore not initialised")
        cur = await self._db.execute(
            "UPDATE device_pair_requests SET token_claimed = 1 "
            "WHERE id = ? AND status = 'accepted' AND token_claimed = 0",
            (pair_request_id,),
        )
        await self._db.commit()
        return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, pair_request_id: str) -> Optional[dict]:
        """Return the record for *pair_request_id*, or ``None``.

        The ``verify_code`` column is ALWAYS stripped from the returned dict so
        the poll route (and any other caller) can never leak it -- it is only
        ever read at creation time, before being discarded.
        """
        if self._db is None:
            raise RuntimeError("DevicePairRequestsStore not initialised")
        cur = await self._db.execute(
            f"SELECT {_SAFE_COLS} FROM device_pair_requests WHERE id = ?",
            (pair_request_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = {k: row[k] for k in row.keys()}
        # Defensive: never surface the code through this read path.
        d.pop("verify_code", None)
        return d

    async def count_pending(self) -> int:
        """Return the TOTAL number of live pending requests (across all
        requesters). Mirrors AuthRequestsStore.count_pending_for but counts
        every pending request -- per-IP limiting alone is defeated by CGNAT /
        distributed floods (F4)."""
        if self._db is None:
            raise RuntimeError("DevicePairRequestsStore not initialised")
        now_iso = _iso(_now())
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM device_pair_requests "
            "WHERE status = 'pending' AND expires_at_ts > ?",
            (now_iso,),
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def list_pending(self) -> list[dict]:
        """Return all live pending requests, oldest first."""
        if self._db is None:
            raise RuntimeError("DevicePairRequestsStore not initialised")
        now_iso = _iso(_now())
        cur = await self._db.execute(
            f"SELECT {_SAFE_COLS} FROM device_pair_requests "
            "WHERE status = 'pending' AND expires_at_ts > ? "
            "ORDER BY created_ts",
            (now_iso,),
        )
        rows = await cur.fetchall()
        out = []
        for row in rows:
            d = {k: row[k] for k in row.keys()}
            d.pop("verify_code", None)
            out.append(d)
        return out
