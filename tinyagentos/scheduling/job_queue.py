"""Job Queue for Resource-Constrained Devices (taOSmd).

Serialises CPU/GPU/NPU-heavy tasks (embedding, LLM extraction, enrichment,
crystallization) on devices like the Orange Pi where resources are shared
across multiple agents.

Design:
  - SQLite-backed persistent queue (survives restarts)
  - Priority levels: urgent (user-triggered) > normal (cron) > background
  - Resource slots: each job declares what it needs (cpu, gpu, npu, memory_mb)
  - Concurrency limits: configurable max concurrent jobs per resource type
  - Simple pull-based: workers call dequeue() to get the next eligible job

Not a distributed task system — this is a single-device queue for one taOS
controller. For cluster-level job distribution, use taOS's worker dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from enum import IntEnum
from pathlib import Path

from tinyagentos.db_migrations import apply_wal_pragmas, run_migrations

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    agent_name TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    resource_type TEXT NOT NULL DEFAULT 'cpu',
    estimated_seconds INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL,
    error TEXT,
    result_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_jobs_agent ON jobs(agent_name);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type);

CREATE TABLE IF NOT EXISTS queue_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Migrations list — version 1 is the baseline schema above.
MIGRATIONS: list = [
    (1, SCHEMA),
]

# Job types
JOB_EMBED = "embed"              # Embed text into vector memory
JOB_EXTRACT = "extract"          # LLM fact extraction
JOB_ENRICH = "enrich"            # LLM session enrichment
JOB_CRYSTALLIZE = "crystallize"  # LLM crystal digest
JOB_SPLIT = "split"              # Session splitter (CPU-only, fast)
JOB_INDEX = "index"              # Full pipeline index_day
JOB_REBUILD = "rebuild"          # Full catalog rebuild

# Resource types — what the job needs
RESOURCE_CPU = "cpu"       # CPU-bound (splitting, regex extraction)
RESOURCE_GPU = "gpu"       # GPU-bound (LLM on GPU worker)
RESOURCE_NPU = "npu"       # NPU-bound (embedding on RK3588, LLM on rkllama)
RESOURCE_EMBED = "embed"   # Embedding model (ONNX or NPU)


class Priority(IntEnum):
    BACKGROUND = 0   # Overnight maintenance, rebuilds
    NORMAL = 1       # Scheduled cron jobs
    URGENT = 2       # User-triggered from UI


# Default concurrency limits per resource type
DEFAULT_LIMITS = {
    RESOURCE_CPU: 2,     # 2 CPU jobs in parallel (Pi has 4x A76 + 4x A55)
    RESOURCE_GPU: 1,     # 1 GPU job at a time
    RESOURCE_NPU: 3,     # 3 NPU jobs in parallel (RK3588 has 3 NPU cores)
    RESOURCE_EMBED: 1,   # 1 embedding job at a time (shared model instance)
}


class JobQueue:
    """SQLite-backed job queue for serialising heavy memory tasks.

    All public methods are async.  Blocking sqlite3 calls are dispatched to a
    thread via asyncio.to_thread so they never stall the event loop.  The
    connection is opened with check_same_thread=False so the same Connection
    object can be handed to different thread-pool workers.
    """

    def __init__(self, db_path: str | Path = "data/job-queue.db"):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._limits: dict[str, int] = dict(DEFAULT_LIMITS)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _sync_init(self) -> None:
        """Open DB, apply WAL + migrations, recover stale jobs."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        apply_wal_pragmas(self._conn)
        run_migrations(self._conn, MIGRATIONS)

        # Load custom limits from config
        for row in self._conn.execute("SELECT key, value FROM queue_config").fetchall():
            if row["key"].startswith("limit_"):
                resource = row["key"][6:]
                try:
                    self._limits[resource] = int(row["value"])
                except ValueError:
                    pass

        # Mark any stale "running" jobs as failed (from a crash/restart)
        stale = self._conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'stale: process restarted' "
            "WHERE status = 'running'"
        )
        if stale.rowcount > 0:
            logger.info("Marked %d stale running jobs as failed", stale.rowcount)
            self._conn.commit()

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._sync_init)

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def _sync_enqueue(
        self,
        job_type: str,
        payload_json: str,
        agent_name: str | None,
        priority: int,
        resource_type: str,
        estimated_seconds: int,
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._conn.execute(
            """INSERT INTO jobs (id, job_type, priority, status, agent_name,
               payload_json, resource_type, estimated_seconds, created_at)
               VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (job_id, job_type, priority, agent_name,
             payload_json, resource_type, estimated_seconds, now),
        )
        self._conn.commit()
        return job_id

    async def enqueue(
        self,
        job_type: str,
        payload: dict | None = None,
        agent_name: str | None = None,
        priority: int = Priority.NORMAL,
        resource_type: str = RESOURCE_CPU,
        estimated_seconds: int = 0,
    ) -> str:
        """Add a job to the queue. Returns job ID."""
        return await asyncio.to_thread(
            self._sync_enqueue,
            job_type,
            json.dumps(payload or {}),
            agent_name,
            priority,
            resource_type,
            estimated_seconds,
        )

    # ------------------------------------------------------------------
    # Dequeue (pull-based)
    # ------------------------------------------------------------------

    def _sync_dequeue(self, resource_types: list[str] | None) -> dict | None:
        """Get the next eligible job to run (sync, called via to_thread)."""
        running: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT resource_type, COUNT(*) as n FROM jobs WHERE status = 'running' GROUP BY resource_type"
        ).fetchall():
            running[row["resource_type"]] = row["n"]

        query = "SELECT * FROM jobs WHERE status = 'pending'"
        params: list = []
        if resource_types:
            placeholders = ",".join("?" * len(resource_types))
            query += f" AND resource_type IN ({placeholders})"
            params.extend(resource_types)
        query += " ORDER BY priority DESC, created_at ASC LIMIT 1"

        candidates = self._conn.execute(query, params).fetchall()
        for row in candidates:
            resource = row["resource_type"]
            limit = self._limits.get(resource, 1)
            current = running.get(resource, 0)
            if current < limit:
                now = time.time()
                self._conn.execute(
                    "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                self._conn.commit()
                claimed = self._conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                return dict(claimed)

        return None

    async def dequeue(self, resource_types: list[str] | None = None) -> dict | None:
        """Get the next eligible job to run.

        Checks concurrency limits for the job's resource type.
        Returns the job dict or None if nothing is eligible.
        """
        return await asyncio.to_thread(self._sync_dequeue, resource_types)

    # ------------------------------------------------------------------
    # Complete / fail
    # ------------------------------------------------------------------

    def _sync_complete(self, job_id: str, result_json: str) -> bool:
        now = time.time()
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'completed', completed_at = ?, result_json = ? "
            "WHERE id = ? AND status = 'running'",
            (now, result_json, job_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def complete(self, job_id: str, result: dict | None = None) -> bool:
        """Mark a job as completed."""
        return await asyncio.to_thread(
            self._sync_complete, job_id, json.dumps(result or {})
        )

    def _sync_fail(self, job_id: str, error: str) -> bool:
        now = time.time()
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'failed', completed_at = ?, error = ? "
            "WHERE id = ? AND status = 'running'",
            (now, error, job_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def fail(self, job_id: str, error: str) -> bool:
        """Mark a job as failed."""
        return await asyncio.to_thread(self._sync_fail, job_id, error)

    def _sync_cancel(self, job_id: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (job_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def cancel(self, job_id: str) -> bool:
        """Cancel a pending job."""
        return await asyncio.to_thread(self._sync_cancel, job_id)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _sync_get_job(self, job_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    async def get_job(self, job_id: str) -> dict | None:
        return await asyncio.to_thread(self._sync_get_job, job_id)

    def _sync_pending_count(self, agent_name: str | None) -> int:
        if agent_name:
            row = self._conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'pending' AND agent_name = ?",
                (agent_name,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'pending'"
            ).fetchone()
        return row["n"]

    async def pending_count(self, agent_name: str | None = None) -> int:
        return await asyncio.to_thread(self._sync_pending_count, agent_name)

    def _sync_running_jobs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = 'running' ORDER BY started_at"
        ).fetchall()
        return [dict(r) for r in rows]

    async def running_jobs(self) -> list[dict]:
        return await asyncio.to_thread(self._sync_running_jobs)

    def _sync_recent(self, limit: int, status: str | None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    async def recent(self, limit: int = 20, status: str | None = None) -> list[dict]:
        return await asyncio.to_thread(self._sync_recent, limit, status)

    def _sync_agent_jobs(self, agent_name: str, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?",
            (agent_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    async def agent_jobs(self, agent_name: str, limit: int = 20) -> list[dict]:
        return await asyncio.to_thread(self._sync_agent_jobs, agent_name, limit)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _sync_set_limit(self, resource_type: str, max_concurrent: int) -> None:
        self._limits[resource_type] = max_concurrent
        self._conn.execute(
            "INSERT INTO queue_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"limit_{resource_type}", str(max_concurrent)),
        )
        self._conn.commit()

    async def set_limit(self, resource_type: str, max_concurrent: int) -> None:
        """Set the concurrency limit for a resource type."""
        await asyncio.to_thread(self._sync_set_limit, resource_type, max_concurrent)

    async def get_limits(self) -> dict[str, int]:
        return dict(self._limits)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _sync_cleanup(self, older_than_days: int) -> int:
        cutoff = time.time() - (older_than_days * 86400)
        cursor = self._conn.execute(
            "DELETE FROM jobs WHERE status IN ('completed', 'failed', 'cancelled') AND created_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    async def cleanup(self, older_than_days: int = 7) -> int:
        """Remove completed/failed/cancelled jobs older than N days."""
        return await asyncio.to_thread(self._sync_cleanup, older_than_days)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def _sync_stats(self) -> dict:
        counts: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT status, COUNT(*) as n FROM jobs GROUP BY status"
        ).fetchall():
            counts[row["status"]] = row["n"]

        running_by_resource: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT resource_type, COUNT(*) as n FROM jobs WHERE status = 'running' GROUP BY resource_type"
        ).fetchall():
            running_by_resource[row["resource_type"]] = row["n"]

        return {
            "counts": counts,
            "running_by_resource": running_by_resource,
            "limits": dict(self._limits),
            "total_pending": counts.get("pending", 0),
            "total_running": counts.get("running", 0),
        }

    async def stats(self) -> dict:
        """Queue statistics."""
        return await asyncio.to_thread(self._sync_stats)
