"""SQLite-backed storage for benchmark results.

One row per (worker_id, capability, model, metric, measured_at). The
scheduler reads the most recent row per (worker_id, capability) for its
cost-model decisions; the UI reads history by (worker_id) for trend
charts and by (capability) for cross-worker leaderboards.

The results table is append-only: a re-run inserts new rows and never
updates or deletes an earlier measurement, so the first-join baseline and
every later run stay comparable.

A second table, ``benchmark_requests``, holds the *queued manual run* for
a worker (at most one per worker). The controller cannot push work to a
worker agent -- the agent is a poller -- so a "Re-run benchmarks" click
writes a row here and the request is handed to the worker in its next
heartbeat response. See :func:`get_pending_request`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from tinyagentos.db_migrations import apply_wal_pragmas_async

# How long a queued manual run stays valid. Past this the request is
# dropped rather than resurrecting a click the user made a quarter of an hour
# ago (or before a worker that has been offline the whole time came back).
# The worker picks a queued run up on its next heartbeat (~5s), so the TTL only
# has to cover a heartbeat hiccup or a worker restart, not a long absence.
REQUEST_TTL_SECONDS = 900.0


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS benchmarks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id       TEXT NOT NULL,
    worker_name     TEXT,
    platform        TEXT,
    capability      TEXT NOT NULL,
    model           TEXT NOT NULL,
    metric          TEXT NOT NULL,
    value           REAL,
    unit            TEXT,
    status          TEXT NOT NULL,
    elapsed_seconds REAL,
    error           TEXT,
    details_json    TEXT,
    suite_name      TEXT,
    first_join      INTEGER DEFAULT 0,
    measured_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_requests (
    worker_id       TEXT PRIMARY KEY,
    requested_at    REAL NOT NULL,
    force           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bench_worker ON benchmarks(worker_id);
CREATE INDEX IF NOT EXISTS idx_bench_cap ON benchmarks(capability);
CREATE INDEX IF NOT EXISTS idx_bench_worker_cap ON benchmarks(worker_id, capability);
CREATE INDEX IF NOT EXISTS idx_bench_measured_at ON benchmarks(measured_at);
"""


class BenchmarkStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.path))
        self._db.row_factory = aiosqlite.Row
        await apply_wal_pragmas_async(self._db)
        await self._db.executescript(CREATE_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def record(
        self,
        *,
        worker_id: str,
        worker_name: Optional[str],
        platform: Optional[str],
        capability: str,
        model: str,
        metric: str,
        value: Optional[float],
        unit: str,
        status: str,
        elapsed_seconds: Optional[float],
        error: Optional[str],
        details: Optional[dict],
        suite_name: Optional[str],
        first_join: bool,
        measured_at: float,
    ) -> int:
        assert self._db is not None, "BenchmarkStore.init() not called"
        cursor = await self._db.execute(
            """
            INSERT INTO benchmarks (
                worker_id, worker_name, platform, capability, model, metric,
                value, unit, status, elapsed_seconds, error, details_json,
                suite_name, first_join, measured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                worker_id,
                worker_name,
                platform,
                capability,
                model,
                metric,
                value,
                unit,
                status,
                elapsed_seconds,
                error,
                json.dumps(details) if details else None,
                suite_name,
                1 if first_join else 0,
                measured_at,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def has_first_join_run(self, worker_id: str) -> bool:
        """True if the worker has already had its first-join benchmark run.

        Used to enforce the "run exactly once on first add, manual after"
        policy. Callers check this before auto-triggering a run; if True,
        they skip the auto-run and only respond to explicit POST triggers.
        """
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM benchmarks WHERE worker_id = ? AND first_join = 1 LIMIT 1",
            (worker_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    # ── queued manual runs ─────────────────────────────────────────────────
    #
    # The worker agent polls (register + heartbeat); there is no worker-side
    # HTTP surface the controller can POST to, so a manual re-run is queued
    # here and delivered in the worker's next heartbeat response.

    async def request_run(
        self,
        *,
        worker_id: str,
        force: bool = False,
        requested_at: Optional[float] = None,
        ttl: float = REQUEST_TTL_SECONDS,
    ) -> Optional[dict]:
        """Queue a manual benchmark run for a worker.

        Returns the queued request, or ``None`` when a live request is already
        queued and ``force`` was not set. One request per worker: a plain repeat
        click is refused rather than stacking a backlog of stale clicks, while
        ``force`` replaces whatever is queued.

        The "already queued" decision is made *inside* the write: a read
        followed by a write would let two concurrent callers both see an empty
        queue and both queue a run, silently dropping one. An expired row does
        not count as queued, so a stale click is replaced -- exactly what a read
        would have said.
        """
        assert self._db is not None, "BenchmarkStore.init() not called"
        ts = time.time() if requested_at is None else float(requested_at)
        cursor = await self._db.execute(
            """
            INSERT INTO benchmark_requests (worker_id, requested_at, force)
            VALUES (?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                requested_at = excluded.requested_at,
                force = excluded.force
            WHERE ? = 1 OR benchmark_requests.requested_at <= ?
            """,
            (worker_id, ts, 1 if force else 0, 1 if force else 0, ts - ttl),
        )
        await self._db.commit()
        # A refused upsert (live non-force request) changes nothing, so
        # rowcount is 0 and the caller answers 409.
        if not cursor.rowcount:
            return None
        return {"worker_id": worker_id, "requested_at": ts, "force": bool(force)}

    async def get_pending_request(
        self,
        worker_id: str,
        *,
        now: Optional[float] = None,
        ttl: float = REQUEST_TTL_SECONDS,
    ) -> Optional[dict]:
        """The queued manual run for a worker, or None.

        A pure read: None means "nothing queued for this worker" -- including
        the case where a queued run has passed its TTL (the expired row is left
        in place; it is inert, replaced by the next :meth:`request_run` and
        removed by :meth:`clear_pending_request`) and the case where the store
        has not been initialised, so a caller on a hot path (the heartbeat) can
        read this without a guard.

        The row deliberately survives delivery: it is cleared only when the run
        it names reports back, so an interrupted run is retried on the next
        heartbeat.
        """
        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT worker_id, requested_at, force FROM benchmark_requests WHERE worker_id = ?",
            (worker_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        request = {
            "worker_id": row["worker_id"],
            "requested_at": float(row["requested_at"]),
            "force": bool(row["force"]),
        }
        if (time.time() if now is None else now) - request["requested_at"] > ttl:
            return None
        return request

    async def clear_pending_request(
        self, worker_id: str, *, requested_at: Optional[float] = None
    ) -> bool:
        """Drop the queued manual run for a worker. True if a row was dropped.

        With ``requested_at`` the deletion is conditional on the id matching,
        and matching happens *inside the statement*. That matters: a report that
        served an earlier run can land just as a newer click replaces the row,
        and a read-then-delete would remove the newer run before the worker ever
        sees it. One conditional DELETE cannot.
        """
        assert self._db is not None, "BenchmarkStore.init() not called"
        if requested_at is None:
            cursor = await self._db.execute(
                "DELETE FROM benchmark_requests WHERE worker_id = ?", (worker_id,)
            )
        else:
            cursor = await self._db.execute(
                "DELETE FROM benchmark_requests WHERE worker_id = ? AND requested_at = ?",
                (worker_id, float(requested_at)),
            )
        await self._db.commit()
        return bool(cursor.rowcount)

    async def latest_by_worker(self, worker_id: str) -> list[dict]:
        """Most recent row per (capability, model) for a given worker."""
        assert self._db is not None
        cursor = await self._db.execute(
            """
            SELECT * FROM benchmarks
            WHERE worker_id = ?
              AND id IN (
                  SELECT MAX(id) FROM benchmarks
                  WHERE worker_id = ?
                  GROUP BY capability, model
              )
            ORDER BY capability, model
            """,
            (worker_id, worker_id),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def history_by_worker(self, worker_id: str, limit: int = 100) -> list[dict]:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT * FROM benchmarks WHERE worker_id = ? ORDER BY measured_at DESC LIMIT ?",
            (worker_id, int(limit)),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def leaderboard(self, capability: str, metric: Optional[str] = None) -> list[dict]:
        """Best-per-worker across the cluster for a given capability."""
        assert self._db is not None
        if metric:
            query = """
                SELECT * FROM benchmarks
                WHERE capability = ? AND metric = ? AND status = 'ok'
                  AND id IN (
                      SELECT MAX(id) FROM benchmarks
                      WHERE capability = ? AND metric = ? AND status = 'ok'
                      GROUP BY worker_id
                  )
                ORDER BY value DESC
            """
            params = (capability, metric, capability, metric)
        else:
            query = """
                SELECT * FROM benchmarks
                WHERE capability = ? AND status = 'ok'
                  AND id IN (
                      SELECT MAX(id) FROM benchmarks
                      WHERE capability = ? AND status = 'ok'
                      GROUP BY worker_id, metric
                  )
                ORDER BY capability, metric, value DESC
            """
            params = (capability, capability)
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        if d.get("details_json"):
            try:
                d["details"] = json.loads(d.pop("details_json"))
            except Exception:
                d["details"] = None
                d.pop("details_json", None)
        else:
            d.pop("details_json", None)
            d["details"] = None
        d["first_join"] = bool(d.get("first_join"))
        return d
