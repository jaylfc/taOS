"""Every conversation turn goes through taOSmd's ``ingest`` (archive + verbatim
embed) with its role and session fields, on every host tier.

taOSmd's ``answer_context`` builds a turn's session window from the vector
rows ``ingest`` writes (``session_id`` / ``turn_index`` / ``session_date`` /
``role``).  A turn that only reached the archive is invisible to it.  This
module is the one path the taOS writers use to get a turn there:

* ``TurnIngestQueue`` is a persisted outbox.  A writer enqueues a turn and
  returns; nothing on the chat path waits for taOSmd.
* A turn is keyed by ``<turn id>|<shelf>``.  ``enqueue`` is INSERT OR IGNORE,
  and a row that reached ``done`` is never sent again, so a retry, a replay and
  a backfill over the same turn cannot double-ingest.
* ``TurnIngestWorker`` drains the queue, rate limited.  A failed send is kept
  with its error, retried with backoff, and after ``MAX_ATTEMPTS`` marked
  ``failed`` and logged at error level.  Nothing is dropped silently; failed
  rows stay until ``requeue_failed``.
* A shelf is a taOSmd agent namespace: an agent's own name for conversations
  it takes part in, ``user_namespace(user_id)`` for a person's own memory.

Each send carries ``source_id`` (the queue key).  A taOSmd that supports it
stores a repeat once; an older one ignores the field, and then, if a send is
accepted but the reply is lost (timeout), the retry can write the turn twice.
Retries after a confirmed success, after a restart and from a backfill never
do either way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from tinyagentos.base_store import BaseStore
from tinyagentos.session_turns import UNKNOWN

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 12
_BACKOFF_BASE = 5.0
_BACKOFF_CAP = 3600.0


# First taOSmd release whose /ingest stores a repeated ``source_id`` once.
SOURCE_ID_DEDUP_MIN = (0, 5, 25)


def source_id_dedup_supported() -> bool:
    """True when the installed taOSmd honours ``source_id`` on ingest."""
    try:
        import taosmd
        parts = []
        for piece in str(taosmd.__version__).split(".")[:3]:
            digits = "".join(c for c in piece if c.isdigit())
            parts.append(int(digits or 0))
        return tuple(parts) >= SOURCE_ID_DEDUP_MIN
    except Exception:
        logger.warning("taosmd version unreadable: assuming no source_id dedup", exc_info=True)
        return False


def role_for(author_type: str | None) -> str:
    """taOSmd role of a taOS author type."""
    if author_type == "user":
        return "user"
    if author_type == "agent":
        return "assistant"
    if author_type == "system":
        return "system"
    return "unknown"


def build_turn(role: str, text: str, fields: dict) -> dict:
    """The turn dict taOSmd's ``ingest`` takes.

    ``fields`` is the dict from ``tinyagentos.session_turns``.  A field that is
    ``UNKNOWN`` is left out (taOSmd then stores the turn without that
    structure) and the gap is logged: it is never replaced by a made-up value.
    """
    turn: dict = {"role": role, "content": text}
    for name in ("session_id", "turn_index", "session_date"):
        value = fields.get(name)
        if value is None or value == UNKNOWN:
            logger.warning("turn ingested without %s (unknown)", name)
            continue
        turn[name] = value
    return turn


def agent_shelves(config, channel: dict | None) -> list[str]:
    """Names of the configured agents that take part in a channel."""
    if not channel:
        return []
    names = {
        str(a.get("name"))
        for a in (getattr(config, "agents", None) or [])
        if isinstance(a, dict) and a.get("name")
    }
    return [m for m in (channel.get("members") or []) if m in names]


class TurnIngestQueue(BaseStore):
    """Persisted outbox of turns waiting to be ingested into taOSmd."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS turn_ingest (
        key TEXT PRIMARY KEY,
        shelf TEXT NOT NULL,
        payload TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        next_attempt_at REAL NOT NULL,
        created_at REAL NOT NULL,
        done_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_turn_ingest_due ON turn_ingest(state, next_attempt_at);
    CREATE TABLE IF NOT EXISTS turn_backfill_cursor (
        source TEXT PRIMARY KEY,
        cursor TEXT NOT NULL
    );
    """

    def __init__(self, db_path):
        super().__init__(db_path)
        self.wake = asyncio.Event()

    async def enqueue(self, key: str, shelf: str, turn: dict) -> bool:
        """Queue a turn.  False when that turn is already queued or done."""
        assert self._db is not None
        now = time.time()
        cur = await self._db.execute(
            "INSERT OR IGNORE INTO turn_ingest "
            "(key, shelf, payload, state, attempts, next_attempt_at, created_at) "
            "VALUES (?, ?, ?, 'pending', 0, ?, ?)",
            (key, shelf, json.dumps(turn), now, now),
        )
        await self._db.commit()
        added = cur.rowcount > 0
        if added:
            self.wake.set()
        return added

    async def exists(self, key: str) -> bool:
        assert self._db is not None
        cur = await self._db.execute("SELECT 1 FROM turn_ingest WHERE key = ?", (key,))
        return await cur.fetchone() is not None

    async def due(self, limit: int = 20) -> list[dict]:
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT key, shelf, payload, attempts FROM turn_ingest "
            "WHERE state = 'pending' AND next_attempt_at <= ? "
            "ORDER BY created_at LIMIT ?",
            (time.time(), limit),
        )
        return [
            {"key": r[0], "shelf": r[1], "turn": json.loads(r[2]), "attempts": r[3]}
            for r in await cur.fetchall()
        ]

    async def mark_inflight(self, key: str) -> None:
        """Record that a send is about to start.  A crash from here until
        ``mark_done`` leaves the row in flight: it may or may not have been sent."""
        assert self._db is not None
        await self._db.execute(
            "UPDATE turn_ingest SET state = 'inflight' WHERE key = ?", (key,))
        await self._db.commit()

    async def settle_inflight(self, *, resend: bool) -> int:
        """Resolve rows left in flight by a crash; returns how many.

        ``resend`` puts them back in the queue; otherwise they are closed out as
        done (they were probably sent, and sending again could duplicate).
        """
        assert self._db is not None
        cur = await self._db.execute(
            "UPDATE turn_ingest SET state = ?, done_at = ? WHERE state = 'inflight'",
            ("pending" if resend else "done", time.time()),
        )
        await self._db.commit()
        if cur.rowcount and resend:
            self.wake.set()
        return cur.rowcount

    async def mark_done(self, key: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE turn_ingest SET state = 'done', done_at = ?, last_error = NULL "
            "WHERE key = ?",
            (time.time(), key),
        )
        await self._db.commit()

    async def mark_attempt_failed(self, key: str, attempts: int, error: str) -> str:
        """Record a failed send; returns the new state (pending or failed)."""
        assert self._db is not None
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            state, delay = "failed", 0.0
        else:
            state, delay = "pending", min(_BACKOFF_BASE * 2 ** attempts, _BACKOFF_CAP)
        await self._db.execute(
            "UPDATE turn_ingest SET state = ?, attempts = ?, last_error = ?, "
            "next_attempt_at = ? WHERE key = ?",
            (state, attempts, error[:500], time.time() + delay, key),
        )
        await self._db.commit()
        return state

    async def requeue_failed(self) -> int:
        """Put every failed turn back in the queue; returns how many."""
        assert self._db is not None
        cur = await self._db.execute(
            "UPDATE turn_ingest SET state = 'pending', attempts = 0, "
            "next_attempt_at = ? WHERE state = 'failed'",
            (time.time(),),
        )
        await self._db.commit()
        if cur.rowcount:
            self.wake.set()
        return cur.rowcount

    async def counts(self) -> dict:
        assert self._db is not None
        cur = await self._db.execute("SELECT state, COUNT(*) FROM turn_ingest GROUP BY state")
        out = {"pending": 0, "inflight": 0, "done": 0, "failed": 0}
        out.update({r[0]: r[1] for r in await cur.fetchall()})
        return out

    async def get_cursor(self, source: str) -> str | None:
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT cursor FROM turn_backfill_cursor WHERE source = ?", (source,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_cursor(self, source: str, cursor: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO turn_backfill_cursor (source, cursor) VALUES (?, ?)",
            (source, cursor),
        )
        await self._db.commit()


async def enqueue_turn(
    queue: TurnIngestQueue | None,
    *,
    turn_id: str | None,
    shelves: list[str],
    role: str,
    text: str,
    fields: dict,
) -> int:
    """Queue one turn for each shelf; returns how many were newly queued.

    Never raises: the chat path must not fail because taOSmd is behind.  A
    failure to queue is logged at error level with the turn's identity (the
    turn is still in the chat store and the archive, and a backfill recovers it).
    """
    if not text or not text.strip() or not shelves:
        return 0
    if queue is None:
        logger.error("turn ingest queue not wired: %s turn not sent to taOSmd", role)
        return 0
    if not turn_id:
        turn_id = f"anon-{uuid.uuid4().hex}"
        logger.warning("turn has no id; it cannot be deduplicated (%s)", turn_id)
    turn = build_turn(role, text, fields)
    added = 0
    for shelf in dict.fromkeys(shelves):
        try:
            if await queue.enqueue(f"{turn_id}|{shelf}", shelf, turn):
                added += 1
        except Exception:
            logger.error(
                "could not queue turn %s for shelf %s", turn_id, shelf, exc_info=True)
    return added


class TurnIngestWorker:
    """Drains the queue into taOSmd, rate limited, with retry and backoff."""

    def __init__(self, queue: TurnIngestQueue, send, *, rate_per_sec: float = 20.0,
                 poll_seconds: float = 5.0):
        self.queue = queue
        self.send = send  # async (shelf, turn, source_id) -> None, raises on failure
        self.min_gap = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task | None = None
        self._stop = False

    async def drain_once(self) -> int:
        """Send every due turn once; returns how many were delivered."""
        delivered = 0
        for item in await self.queue.due(50):
            await self.queue.mark_inflight(item["key"])
            try:
                await self.send(item["shelf"], item["turn"], item["key"])
            except Exception as exc:
                state = await self.queue.mark_attempt_failed(
                    item["key"], item["attempts"], f"{type(exc).__name__}: {exc}")
                log = logger.error if state == "failed" else logger.warning
                log("taOSmd ingest of %s to shelf %s failed (%s), now %s",
                    item["key"], item["shelf"], exc, state)
            else:
                await self.queue.mark_done(item["key"])
                delivered += 1
            if self.min_gap:
                await asyncio.sleep(self.min_gap)
        return delivered

    async def recover_inflight(self) -> int:
        """Settle rows a crash left in flight (see ``settle_inflight``)."""
        resend = source_id_dedup_supported()
        settled = await self.queue.settle_inflight(resend=resend)
        if settled:
            logger.warning(
                "%d turn(s) were in flight at the last stop and are maybe sent; "
                "%s", settled,
                "sending again (taOSmd dedups on source_id)" if resend else
                "marked done without re-sending (this taOSmd would store a repeat)")
        return settled

    async def _run(self) -> None:
        try:
            await self.recover_inflight()
        except Exception:
            logger.error("turn ingest recovery failed", exc_info=True)
        while not self._stop:
            try:
                # Keep going while anything is due (a backfill can queue
                # thousands); the rate limit paces each send.
                while await self.drain_once():
                    pass
            except Exception:
                logger.error("turn ingest worker error", exc_info=True)
            self.queue.wake.clear()
            try:
                await asyncio.wait_for(self.queue.wake.wait(), self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._stop = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        self.queue.wake.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def http_sender(client_fn, base_fn, timeout: float = 30.0):
    """A ``send`` for the worker: POST the turn to taOSmd's ``/ingest``."""

    async def send(shelf: str, turn: dict, source_id: str) -> None:
        # source_id is the queue key (<turn id>|<shelf>): taOSmd stores a
        # repeat of it once (newer taOSmd; older versions ignore the field).
        body = {"text": turn["content"], "agent": shelf, "source_id": source_id}
        for name in ("role", "session_id", "turn_index", "session_date"):
            if name in turn:
                body[name] = turn[name]
        resp = await client_fn().post(f"{base_fn()}/ingest", json=body, timeout=timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"taosmd /ingest returned {resp.status_code}")

    return send


def taosmd_base_url(app_state) -> str:
    """Base URL of the taOSmd service (same precedence the user-memory routes use)."""
    explicit = getattr(app_state, "taosmd_url", None)
    if explicit:
        return explicit
    config = getattr(app_state, "config", None)
    if config and getattr(config, "memory_url", None):
        return config.memory_url
    return "http://localhost:7900"
