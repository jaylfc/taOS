"""Per-session turn counter and the session fields every memory write carries.

Every place taOS writes conversation memory to taOSmd (the archive, the user
memory store) stamps the record with four fields so a later reader can order
turns inside a session and place them in time:

    session_id    which conversation (chat channel id, or a platform channel)
    turn_index    position of the turn inside the session, 0-based
    session_date  UTC date (YYYY-MM-DD) of the turn's own time, never ingest time
    turn_owner    who authored the turn (user id, agent slug, platform sender)

Rules (one definition, used by every writer):

* A turn is one conversation message.  Its index is allocated ONCE, before the
  memory write, and reused for every write of that turn (the user-memory
  capture and the archive record of one chat post share one index).
* Allocation is a single UPSERT ... RETURNING statement on a persisted table,
  so two concurrent posts can never share an index and a restart continues the
  sequence.  A count-then-insert would not be atomic.
* Events that belong to a turn but are not turns themselves (tool calls, tool
  results, reasoning, errors) carry the index of the session's latest turn.
* Anything that cannot be determined is written as ``UNKNOWN`` and logged at
  warning level.  Never 0, never an empty string.
* A session that has history from before this counter existed is seeded on
  first use from ``seed`` (for chat, the number of messages already stored), so
  the first new turn continues the real position instead of restarting at 0.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

SeedFn = Callable[[], Awaitable[int]]


class SessionTurnStore(BaseStore):
    """Persisted per-session turn counter."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS session_turns (
        session_id TEXT PRIMARY KEY,
        last_index INTEGER NOT NULL
    );
    """

    def __init__(self, db_path):
        super().__init__(db_path)
        # Serialises the seed-then-increment pair for one process.  The
        # increment itself is atomic in SQL; the lock only makes the one-time
        # seed (an INSERT OR IGNORE) and the increment a unit.
        self._lock = asyncio.Lock()

    async def next_turn(self, session_id: str, seed: SeedFn | None = None) -> int:
        """Allocate and return the index of the turn about to be written."""
        if not session_id:
            raise ValueError("session_id is required")
        assert self._db is not None
        async with self._lock:
            if seed is not None:
                cur = await self._db.execute(
                    "SELECT 1 FROM session_turns WHERE session_id = ?", (session_id,)
                )
                if await cur.fetchone() is None:
                    existing = int(await seed())
                    await self._db.execute(
                        "INSERT OR IGNORE INTO session_turns (session_id, last_index) "
                        "VALUES (?, ?)",
                        (session_id, existing - 1),
                    )
            cur = await self._db.execute(
                "INSERT INTO session_turns (session_id, last_index) VALUES (?, 0) "
                "ON CONFLICT(session_id) DO UPDATE SET last_index = last_index + 1 "
                "RETURNING last_index",
                (session_id,),
            )
            row = await cur.fetchone()
            await self._db.commit()
        return int(row[0])

    async def current_turn(self, session_id: str) -> int | None:
        """Index of the session's latest turn, or None if it has none yet."""
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT last_index FROM session_turns WHERE session_id = ?", (session_id,)
        )
        row = await cur.fetchone()
        if row is None or row[0] < 0:
            return None
        return int(row[0])


def session_date(timestamp: float | None, *, what: str = "turn") -> str:
    """UTC date of a turn's own time, or UNKNOWN (logged) if it is unusable."""
    try:
        if timestamp is None:
            raise ValueError("no timestamp")
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        logger.warning("session_date unknown for %s (timestamp=%r): %s", what, timestamp, exc)
        return UNKNOWN


def _owner(owner: str | None, session_id: str) -> str:
    if isinstance(owner, str) and owner:
        return owner
    logger.warning("turn_owner unknown for session %s", session_id)
    return UNKNOWN


def _session(session_id: str | None) -> str | None:
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


async def stamp_turn(
    store: SessionTurnStore | None,
    session_id: str | None,
    *,
    owner: str | None,
    timestamp: float | None = None,
    seed: SeedFn | None = None,
) -> dict:
    """Allocate a turn and return its session fields.

    ``timestamp`` is the turn's own time; omitted means the turn happens now.
    Never raises: a failure is logged and the affected field is UNKNOWN.
    """
    ts = time.time() if timestamp is None else timestamp
    sid = _session(session_id)
    fields = {
        "session_id": sid or UNKNOWN,
        "turn_index": UNKNOWN,
        "session_date": session_date(ts),
        "turn_owner": _owner(owner, sid or UNKNOWN),
    }
    if sid is None:
        logger.warning("session_id unknown: turn_index not allocated")
        return fields
    if store is None:
        logger.warning("session turn counter not wired: turn_index unknown for %s", sid)
        return fields
    try:
        fields["turn_index"] = await store.next_turn(sid, seed=seed)
    except Exception:
        logger.warning("turn_index allocation failed for session %s", sid, exc_info=True)
    return fields


async def stamp_event(
    store: SessionTurnStore | None,
    session_id: str | None,
    *,
    owner: str | None,
    timestamp: float | None = None,
) -> dict:
    """Session fields for an event inside the session's latest turn."""
    ts = time.time() if timestamp is None else timestamp
    sid = _session(session_id)
    fields = {
        "session_id": sid or UNKNOWN,
        "turn_index": UNKNOWN,
        "session_date": session_date(ts),
        "turn_owner": _owner(owner, sid or UNKNOWN),
    }
    if sid is None:
        logger.warning("session_id unknown: turn_index unknown for event")
        return fields
    if store is None:
        logger.warning("session turn counter not wired: turn_index unknown for %s", sid)
        return fields
    try:
        current = await store.current_turn(sid)
    except Exception:
        logger.warning("turn_index lookup failed for session %s", sid, exc_info=True)
        return fields
    if current is None:
        logger.warning("no turn recorded yet for session %s: turn_index unknown", sid)
    else:
        fields["turn_index"] = current
    return fields
