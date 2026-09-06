from __future__ import annotations
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
import aiosqlite
from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)


@dataclass
class TrainingJob:
    id: str = ""
    agent_name: str | None = None
    base_model: str = ""
    dataset_description: str = ""
    config: dict = field(default_factory=dict)
    status: str = "queued"  # queued | preparing | training | converting | deploying | complete | failed
    worker_name: str = ""
    progress: float = 0.0
    metrics: dict = field(default_factory=dict)
    output_path: str = ""
    error: str = ""
    created_at: float = 0
    completed_at: float = 0


TRAINING_SCHEMA = """
CREATE TABLE IF NOT EXISTS training_jobs (
    id TEXT PRIMARY KEY,
    agent_name TEXT,
    base_model TEXT NOT NULL,
    dataset_description TEXT DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    worker_name TEXT DEFAULT '',
    progress REAL DEFAULT 0.0,
    metrics TEXT DEFAULT '{}',
    output_path TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS training_presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    config TEXT NOT NULL
);
"""


class TrainingManager(BaseStore):
    SCHEMA = TRAINING_SCHEMA

    async def _post_init(self):
        # Seed default presets
        presets = [
            ("quick", "Quick", "1 epoch, small LoRA rank -- fast results, lower quality",
             {"epochs": 1, "lora_rank": 8, "lora_alpha": 16, "learning_rate": 2e-4, "batch_size": 4}),
            ("balanced", "Balanced", "3 epochs, medium rank -- good balance",
             {"epochs": 3, "lora_rank": 16, "lora_alpha": 32, "learning_rate": 1e-4, "batch_size": 4}),
            ("thorough", "Thorough", "5 epochs, larger rank -- best quality, slower",
             {"epochs": 5, "lora_rank": 32, "lora_alpha": 64, "learning_rate": 5e-5, "batch_size": 2}),
        ]
        for pid, name, desc, config in presets:
            await self._db.execute(
                "INSERT OR IGNORE INTO training_presets (id, name, description, config) VALUES (?, ?, ?, ?)",
                (pid, name, desc, json.dumps(config)),
            )
        await self._db.commit()

    async def create_job(self, base_model: str, agent_name: str | None = None,
                         dataset_description: str = "", config: dict | None = None) -> str:
        import uuid
        if config is None:
            config = {}
        job_id = str(uuid.uuid4())[:8]
        now = time.time()
        await self._db.execute(
            """INSERT INTO training_jobs
               (id, agent_name, base_model, dataset_description, config, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?)""",
            (job_id, agent_name, base_model, dataset_description, json.dumps(config), now),
        )
        await self._db.commit()
        return job_id

    async def get_job(self, job_id: str) -> TrainingJob | None:
        async with self._db.execute(
            "SELECT * FROM training_jobs WHERE id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))
        data["config"] = json.loads(data["config"]) if isinstance(data["config"], str) else data["config"]
        data["metrics"] = json.loads(data["metrics"]) if isinstance(data["metrics"], str) else data["metrics"]
        return TrainingJob(**data)

    async def list_jobs(self, agent_name: str | None = None) -> list[dict]:
        sql = "SELECT id, agent_name, base_model, status, progress, worker_name, created_at, completed_at FROM training_jobs"
        params: list = []
        if agent_name:
            sql += " WHERE agent_name = ?"
            params.append(agent_name)
        sql += " ORDER BY created_at DESC"
        async with self._db.execute(sql, params) as cursor:
            return [dict(zip([d[0] for d in cursor.description], row)) for row in await cursor.fetchall()]

    async def update_job(self, job_id: str, **kwargs):
        for field_name in ["status", "progress", "worker_name", "metrics", "output_path", "error", "completed_at"]:
            if field_name in kwargs:
                value = kwargs[field_name]
                if field_name == "metrics" and isinstance(value, dict):
                    value = json.dumps(value)
                await self._db.execute(f"UPDATE training_jobs SET {field_name} = ? WHERE id = ?", (value, job_id))
        await self._db.commit()

    async def delete_job(self, job_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM training_jobs WHERE id = ?", (job_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_presets(self) -> list[dict]:
        async with self._db.execute("SELECT id, name, description, config FROM training_presets") as cursor:
            return [{"id": r[0], "name": r[1], "description": r[2], "config": json.loads(r[3])} for r in await cursor.fetchall()]
