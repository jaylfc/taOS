from __future__ import annotations

import time
import uuid
from pathlib import Path

from tinyagentos.base_store import BaseStore


class ExpertAgentStore(BaseStore):
    """SQLite-backed store for hidden app-expert agents."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS expert_agents (
        id TEXT PRIMARY KEY,
        app_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT 'qwen3-4b',
        color TEXT NOT NULL DEFAULT '#888888',
        computer_use TEXT NOT NULL DEFAULT 'optional',
        created_at REAL NOT NULL
    );
    """

    def _row_to_dict(self, row) -> dict:
        return {
            "id": row[0],
            "app_id": row[1],
            "name": row[2],
            "system_prompt": row[3],
            "model": row[4],
            "color": row[5],
            "computer_use": row[6],
            "created_at": row[7],
        }

    async def get_or_create(
        self,
        app_id: str,
        name: str,
        system_prompt: str,
        model: str,
        color: str,
        computer_use: str,
    ) -> dict:
        """Return existing expert agent for app_id, or create one."""
        existing = await self.get_by_app(app_id)
        if existing is not None:
            return existing

        agent_id = str(uuid.uuid4())
        created_at = time.time()
        await self._db.execute(
            """
            INSERT INTO expert_agents (id, app_id, name, system_prompt, model, color, computer_use, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, app_id, name, system_prompt, model, color, computer_use, created_at),
        )
        await self._db.commit()
        return {
            "id": agent_id,
            "app_id": app_id,
            "name": name,
            "system_prompt": system_prompt,
            "model": model,
            "color": color,
            "computer_use": computer_use,
            "created_at": created_at,
        }

    async def get_by_app(self, app_id: str) -> dict | None:
        """Return expert agent dict for app_id, or None if not found."""
        async with self._db.execute(
            "SELECT id, app_id, name, system_prompt, model, color, computer_use, created_at FROM expert_agents WHERE app_id = ?",
            (app_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def update_prompt(self, app_id: str, system_prompt: str) -> None:
        """Update the system prompt for an expert agent."""
        await self._db.execute(
            "UPDATE expert_agents SET system_prompt = ? WHERE app_id = ?",
            (system_prompt, app_id),
        )
        await self._db.commit()

    async def reset(self, app_id: str) -> None:
        """Delete expert agent entry (will be recreated with defaults on next launch)."""
        await self._db.execute(
            "DELETE FROM expert_agents WHERE app_id = ?",
            (app_id,),
        )
        await self._db.commit()

    async def list_all(self) -> list[dict]:
        """Return all expert agents."""
        async with self._db.execute(
            "SELECT id, app_id, name, system_prompt, model, color, computer_use, created_at FROM expert_agents ORDER BY created_at"
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]
