from __future__ import annotations

import json
import time
import uuid

from tinyagentos.base_store import BaseStore

CONVERSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversion_jobs (
    id TEXT PRIMARY KEY,
    source_model TEXT NOT NULL,
    source_format TEXT NOT NULL,
    target_format TEXT NOT NULL,
    target_quantization TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    worker_name TEXT DEFAULT '',
    progress REAL DEFAULT 0.0,
    output_path TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    completed_at REAL
);
"""

CONVERSION_PATHS = [
    {"from": "gguf", "to": "rkllm", "capability": "rknn-conversion", "description": "GGUF to Rockchip NPU format"},
    {"from": "huggingface", "to": "gguf", "capability": None, "description": "HuggingFace to quantized GGUF"},
    {"from": "gguf", "to": "mlx", "capability": None, "description": "GGUF to Apple MLX format"},
    {"from": "safetensors", "to": "gguf", "capability": None, "description": "SafeTensors to quantized GGUF"},
    {"from": "safetensors", "to": "rkllm", "capability": "rknn-conversion", "description": "SafeTensors to RKLLM via RKNN toolkit"},
]


class ConversionManager(BaseStore):
    SCHEMA = CONVERSION_SCHEMA

    async def create_job(self, source_model: str, source_format: str,
                         target_format: str, target_quantization: str = "") -> str:
        job_id = str(uuid.uuid4())[:8]
        now = time.time()
        await self._db.execute(
            "INSERT INTO conversion_jobs (id, source_model, source_format, target_format, target_quantization, status, created_at) VALUES (?, ?, ?, ?, ?, 'queued', ?)",
            (job_id, source_model, source_format, target_format, target_quantization, now))
        await self._db.commit()
        return job_id

    async def get_job(self, job_id: str) -> dict | None:
        async with self._db.execute("SELECT * FROM conversion_jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return dict(zip([d[0] for d in cursor.description], row))

    async def list_jobs(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, source_model, source_format, target_format, status, progress, worker_name, created_at FROM conversion_jobs ORDER BY created_at DESC"
        ) as cursor:
            return [dict(zip([d[0] for d in cursor.description], row)) for row in await cursor.fetchall()]

    async def update_job(self, job_id: str, **kwargs):
        for field in ["status", "progress", "worker_name", "output_path", "error", "completed_at"]:
            if field in kwargs:
                await self._db.execute(f"UPDATE conversion_jobs SET {field} = ? WHERE id = ?", (kwargs[field], job_id))
        await self._db.commit()

    async def delete_job(self, job_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM conversion_jobs WHERE id = ?", (job_id,))
        await self._db.commit()
        return cursor.rowcount > 0
