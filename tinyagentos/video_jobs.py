# tinyagentos/video_jobs.py
"""Persistent store for async video generation jobs.

Mirrors tinyagentos/conversion.py's ConversionManager: a thin BaseStore
subclass that tracks job status/result so a status poll survives across
requests (and controller restarts). routes/video.py enqueues a job here,
runs the actual generation in a background task, and updates the row as the
job progresses -- see routes/video.py's module docstring for the full flow.
"""
from __future__ import annotations

import time
import uuid

import aiosqlite

from tinyagentos.base_store import BaseStore

VIDEO_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_jobs (
    -- id is a full uuid4 hex (32 chars). It must NOT be truncated: the id is a
    -- persistent PRIMARY KEY, and a shortened id collides (a collision would
    -- raise IntegrityError and surface as a 500 to the enqueue caller).
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL DEFAULT 0.0,
    result_json TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    completed_at REAL
);
"""


class VideoJobStore(BaseStore):
    SCHEMA = VIDEO_JOBS_SCHEMA

    async def create_job(self) -> str:
        job_id = uuid.uuid4().hex
        await self._db.execute(
            "INSERT INTO video_jobs (id, status, created_at) VALUES (?, 'queued', ?)",
            (job_id, time.time()),
        )
        await self._db.commit()
        return job_id

    async def get_job(self, job_id: str) -> dict | None:
        # Map columns by name via a dict row factory rather than zipping
        # cursor.description positionally -- decoupled from column order.
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM video_jobs WHERE id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_job(self, job_id: str, **kwargs) -> None:
        fields = [f for f in ("status", "progress", "result_json", "error", "completed_at") if f in kwargs]
        if not fields:
            return
        await self._db.execute(
            f"UPDATE video_jobs SET {', '.join(f'{f} = ?' for f in fields)} WHERE id = ?",
            (*(kwargs[f] for f in fields), job_id),
        )
        await self._db.commit()
