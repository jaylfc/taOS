from __future__ import annotations

import json
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

SCHEDULER_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    agent_name TEXT,
    schedule TEXT NOT NULL,
    command TEXT NOT NULL,
    description TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run INTEGER,
    next_run INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    tasks TEXT NOT NULL
);
"""

# Default presets
DEFAULT_PRESETS = [
    {
        "name": "Daily Memory Maintenance",
        "description": "Embed new transcripts and clean up old memories daily",
        "tasks": [
            {"name": "Daily Embed", "schedule": "0 2 * * *", "command": "qmd embed", "description": "Embed new content at 2am"},
            {"name": "Memory Cleanup", "schedule": "0 3 * * *", "command": "qmd maintenance --cleanup", "description": "Clean orphaned vectors at 3am"},
        ],
    },
    {
        "name": "Health Checks",
        "description": "Regular health monitoring tasks",
        "tasks": [
            {"name": "Backend Ping", "schedule": "*/5 * * * *", "command": "curl -sf http://localhost:7833/health", "description": "Check rkllama every 5 min"},
        ],
    },
]


class TaskScheduler(BaseStore):
    SCHEMA = SCHEDULER_SCHEMA

    async def _post_init(self) -> None:
        # Seed default presets
        for preset in DEFAULT_PRESETS:
            await self._db.execute(
                "INSERT OR IGNORE INTO task_presets (name, description, tasks) VALUES (?, ?, ?)",
                (preset["name"], preset["description"], json.dumps(preset["tasks"])),
            )
        await self._db.commit()

    async def add_task(self, name: str, schedule: str, command: str, agent_name: str | None = None, description: str = "") -> int:
        now = int(time.time())
        cursor = await self._db.execute(
            "INSERT INTO scheduled_tasks (name, agent_name, schedule, command, description, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, agent_name, schedule, command, description, now),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def list_tasks(self, agent_name: str | None = None) -> list[dict]:
        sql = "SELECT id, name, agent_name, schedule, command, description, enabled, last_run, next_run FROM scheduled_tasks"
        params = []
        if agent_name:
            sql += " WHERE agent_name = ?"
            params.append(agent_name)
        sql += " ORDER BY name"
        async with self._db.execute(sql, params) as cursor:
            return [{"id": r[0], "name": r[1], "agent_name": r[2], "schedule": r[3],
                     "command": r[4], "description": r[5], "enabled": bool(r[6]),
                     "last_run": r[7], "next_run": r[8]} for r in await cursor.fetchall()]

    async def update_task(self, task_id: int, **kwargs):
        for field in ["name", "schedule", "command", "description"]:
            if field in kwargs:
                await self._db.execute(f"UPDATE scheduled_tasks SET {field} = ? WHERE id = ?", (kwargs[field], task_id))
        if "enabled" in kwargs:
            await self._db.execute("UPDATE scheduled_tasks SET enabled = ? WHERE id = ?", (int(kwargs["enabled"]), task_id))
        await self._db.commit()

    async def delete_task(self, task_id: int) -> bool:
        cursor = await self._db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_presets(self) -> list[dict]:
        async with self._db.execute("SELECT id, name, description, tasks FROM task_presets ORDER BY name") as cursor:
            return [{"id": r[0], "name": r[1], "description": r[2], "tasks": json.loads(r[3])} for r in await cursor.fetchall()]

    async def apply_preset(self, preset_id: int, agent_name: str) -> int:
        async with self._db.execute("SELECT tasks FROM task_presets WHERE id = ?", (preset_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return 0
        tasks = json.loads(row[0])
        count = 0
        for task in tasks:
            await self.add_task(task["name"], task["schedule"], task["command"], agent_name, task.get("description", ""))
            count += 1
        return count
