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

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path

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
    """SQLite-backed job queue for serialising heavy memory tasks."""

    def __init__(self, db_path: str | Path = "data/job-queue.db"):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._limits: dict[str, int] = dict(DEFAULT_LIMITS)

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None puts the connection in autocommit mode so we
        # can issue BEGIN IMMEDIATE manually for atomic read-check-write.
        self._conn = sqlite3.connect(self._db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

        # Load custom limits from config
        for row in self._conn.execute("SELECT key, value FROM queue_config").fetchall():
            if row["key"].startswith("limit_"):
                resource = row["key"][6:]
                try:
                    self._limits[resource] = int(row["value"])
                except ValueError:
                    pass

        # Mark any stale "running" jobs as failed (from a crash/restart)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            stale = self._conn.execute(
                "UPDATE jobs SET status = 'failed', error = 'stale: process restarted' "
                "WHERE status = 'running'"
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        if stale.rowcount > 0:
            logger.info("Marked %d stale running jobs as failed", stale.rowcount)

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

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
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._conn.execute(
            """INSERT INTO jobs (id, job_type, priority, status, agent_name,
               payload_json, resource_type, estimated_seconds, created_at)
               VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (job_id, job_type, priority, agent_name,
             json.dumps(payload or {}), resource_type, estimated_seconds, now),
        )
        return job_id

    # ------------------------------------------------------------------
    # Dequeue (pull-based)
    # ------------------------------------------------------------------

    async def dequeue(self, resource_types: list[str] | None = None) -> dict | None:
        """Get the next eligible job to run, atomically.

        Uses BEGIN IMMEDIATE to acquire a write lock before the read-check-update
        sequence so two concurrent callers cannot both claim the same job.

        Returns the job dict or None if nothing is eligible.
        """
        query = "SELECT * FROM jobs WHERE status = 'pending'"
        params: list = []
        if resource_types:
            placeholders = ",".join("?" * len(resource_types))
            query += f" AND resource_type IN ({placeholders})"
            params.extend(resource_types)
        query += " ORDER BY priority DESC, created_at ASC"

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # Count currently running jobs per resource type (inside the lock)
            running: dict[str, int] = {}
            for row in self._conn.execute(
                "SELECT resource_type, COUNT(*) as n FROM jobs WHERE status = 'running' GROUP BY resource_type"
            ).fetchall():
                running[row["resource_type"]] = row["n"]

            # Walk pending jobs in priority order and claim the first one with capacity
            for row in self._conn.execute(query, params).fetchall():
                resource = row["resource_type"]
                limit = self._limits.get(resource, 1)
                current = running.get(resource, 0)
                if current < limit:
                    now = time.time()
                    self._conn.execute(
                        "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                    claimed = self._conn.execute(
                        "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                    ).fetchone()
                    self._conn.execute("COMMIT")
                    return dict(claimed)

            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        return None

    # ------------------------------------------------------------------
    # Complete / fail
    # ------------------------------------------------------------------

    async def complete(self, job_id: str, result: dict | None = None) -> bool:
        """Mark a job as completed."""
        now = time.time()
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'completed', completed_at = ?, result_json = ? WHERE id = ? AND status = 'running'",
            (now, json.dumps(result or {}), job_id),
        )
        return cursor.rowcount > 0

    async def fail(self, job_id: str, error: str) -> bool:
        """Mark a job as failed."""
        now = time.time()
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'failed', completed_at = ?, error = ? WHERE id = ? AND status = 'running'",
            (now, error, job_id),
        )
        return cursor.rowcount > 0

    async def cancel(self, job_id: str) -> bool:
        """Cancel a pending job."""
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (job_id,),
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_job(self, job_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    async def pending_count(self, agent_name: str | None = None) -> int:
        if agent_name:
            row = self._conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status = 'pending' AND agent_name = ?",
                (agent_name,),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) as n FROM jobs WHERE status = 'pending'").fetchone()
        return row["n"]

    async def running_jobs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = 'running' ORDER BY started_at"
        ).fetchall()
        return [dict(r) for r in rows]

    async def recent(self, limit: int = 20, status: str | None = None) -> list[dict]:
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

    async def agent_jobs(self, agent_name: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?",
            (agent_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def set_limit(self, resource_type: str, max_concurrent: int) -> None:
        """Set the concurrency limit for a resource type."""
        self._limits[resource_type] = max_concurrent
        self._conn.execute(
            "INSERT INTO queue_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"limit_{resource_type}", str(max_concurrent)),
        )

    async def get_limits(self) -> dict[str, int]:
        return dict(self._limits)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self, older_than_days: int = 7) -> int:
        """Remove completed/failed/cancelled jobs older than N days."""
        cutoff = time.time() - (older_than_days * 86400)
        cursor = self._conn.execute(
            "DELETE FROM jobs WHERE status IN ('completed', 'failed', 'cancelled') AND created_at < ?",
            (cutoff,),
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def stats(self) -> dict:
        """Queue statistics."""
        counts = {}
        for row in self._conn.execute(
            "SELECT status, COUNT(*) as n FROM jobs GROUP BY status"
        ).fetchall():
            counts[row["status"]] = row["n"]

        running_by_resource = {}
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
