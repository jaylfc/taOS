from __future__ import annotations

import time
import uuid
from pathlib import Path

from tinyagentos.base_store import BaseStore

STREAMING_SCHEMA = """
CREATE TABLE IF NOT EXISTS streaming_sessions (
    session_id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    agent_type TEXT NOT NULL DEFAULT 'app-expert',
    worker_name TEXT NOT NULL DEFAULT 'local',
    container_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'starting',
    started_at REAL NOT NULL,
    last_activity REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stream_status ON streaming_sessions(status);
"""

_ACTIVE_STATUSES = ("starting", "running", "paused")


def _row_to_dict(row: tuple) -> dict:
    return {
        "session_id": row[0],
        "app_id": row[1],
        "agent_name": row[2],
        "agent_type": row[3],
        "worker_name": row[4],
        "container_id": row[5],
        "status": row[6],
        "started_at": row[7],
        "last_activity": row[8],
    }


class StreamingSessionStore(BaseStore):
    SCHEMA = STREAMING_SCHEMA

    async def create_session(
        self,
        app_id: str,
        agent_name: str,
        agent_type: str,
        worker_name: str,
        container_id: str,
    ) -> str:
        session_id = uuid.uuid4().hex[:12]
        now = time.time()
        await self._db.execute(
            """
            INSERT INTO streaming_sessions
                (session_id, app_id, agent_name, agent_type, worker_name,
                 container_id, status, started_at, last_activity)
            VALUES (?, ?, ?, ?, ?, ?, 'starting', ?, ?)
            """,
            (session_id, app_id, agent_name, agent_type, worker_name, container_id, now, now),
        )
        await self._db.commit()
        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        async with self._db.execute(
            """
            SELECT session_id, app_id, agent_name, agent_type, worker_name,
                   container_id, status, started_at, last_activity
            FROM streaming_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    async def list_sessions(self, active_only: bool = False) -> list[dict]:
        if active_only:
            placeholders = ",".join("?" * len(_ACTIVE_STATUSES))
            sql = f"""
                SELECT session_id, app_id, agent_name, agent_type, worker_name,
                       container_id, status, started_at, last_activity
                FROM streaming_sessions
                WHERE status IN ({placeholders})
                ORDER BY started_at DESC
            """
            params = list(_ACTIVE_STATUSES)
        else:
            sql = """
                SELECT session_id, app_id, agent_name, agent_type, worker_name,
                       container_id, status, started_at, last_activity
                FROM streaming_sessions
                ORDER BY started_at DESC
            """
            params = []
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def update_status(self, session_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE streaming_sessions SET status = ? WHERE session_id = ?",
            (status, session_id),
        )
        await self._db.commit()

    async def swap_agent(self, session_id: str, agent_name: str, agent_type: str) -> None:
        await self._db.execute(
            "UPDATE streaming_sessions SET agent_name = ?, agent_type = ? WHERE session_id = ?",
            (agent_name, agent_type, session_id),
        )
        await self._db.commit()

    async def touch_activity(self, session_id: str) -> None:
        await self._db.execute(
            "UPDATE streaming_sessions SET last_activity = ? WHERE session_id = ?",
            (time.time(), session_id),
        )
        await self._db.commit()

    async def delete_session(self, session_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM streaming_sessions WHERE session_id = ?",
            (session_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0
