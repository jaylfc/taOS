"""SQLite-backed storage for benchmark results.

One row per (worker_id, capability, model, metric, measured_at). The
scheduler reads the most recent row per (worker_id, capability) for its
cost-model decisions; the UI reads history by (worker_id) for trend
charts and by (capability) for cross-worker leaderboards.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import aiosqlite

from tinyagentos.db_migrations import apply_wal_pragmas_async


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
