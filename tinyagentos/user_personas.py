"""SQLite-backed store for user-authored personas."""
from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from tinyagentos.db_migrations import apply_wal_pragmas

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_personas (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    soul_md     TEXT NOT NULL DEFAULT '',
    agent_md    TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL
);
"""


class UserPersonaStore:
    def __init__(self, db_path: Path):
        self._db = Path(db_path)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            apply_wal_pragmas(con)
            con.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------
    # Sync helpers — used internally and wrapped in to_thread below
    # ------------------------------------------------------------------

    def _sync_create(
        self,
        *,
        name: str,
        soul_md: str,
        agent_md: str = "",
        description: str | None = None,
    ) -> str:
        pid = uuid.uuid4().hex
        with self._conn() as con:
            con.execute(
                "INSERT INTO user_personas (id, name, description, soul_md, agent_md, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pid, name, description, soul_md, agent_md, int(time.time())),
            )
        return pid

    def _sync_get(self, pid: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT id, name, description, soul_md, agent_md, created_at "
                "FROM user_personas WHERE id = ?",
                (pid,),
            ).fetchone()
        return dict(row) if row else None

    def _sync_list(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT id, name, description, soul_md, agent_md, created_at "
                "FROM user_personas ORDER BY created_at DESC, rowid DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def _sync_update(self, pid: str, **fields) -> None:
        allowed = {"name", "description", "soul_md", "agent_md"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown fields: {sorted(bad)}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [pid]
        with self._conn() as con:
            con.execute(f"UPDATE user_personas SET {assignments} WHERE id = ?", values)

    def _sync_delete(self, pid: str) -> None:
        with self._conn() as con:
            con.execute("DELETE FROM user_personas WHERE id = ?", (pid,))

    # ------------------------------------------------------------------
    # Public API — sync wrappers kept for backward-compat (non-async callers),
    # async variants added for use from FastAPI route handlers.
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        soul_md: str,
        agent_md: str = "",
        description: str | None = None,
    ) -> str:
        return self._sync_create(
            name=name, soul_md=soul_md, agent_md=agent_md, description=description
        )

    def get(self, pid: str) -> dict[str, Any] | None:
        return self._sync_get(pid)

    def list(self) -> list[dict[str, Any]]:
        return self._sync_list()

    def update(self, pid: str, **fields) -> None:
        return self._sync_update(pid, **fields)

    def delete(self, pid: str) -> None:
        return self._sync_delete(pid)

    # Async variants for use inside FastAPI async route handlers
    async def acreate(
        self,
        *,
        name: str,
        soul_md: str,
        agent_md: str = "",
        description: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._sync_create,
            name=name, soul_md=soul_md, agent_md=agent_md, description=description,
        )

    async def aget(self, pid: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._sync_get, pid)

    async def alist(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._sync_list)

    async def aupdate(self, pid: str, **fields) -> None:
        return await asyncio.to_thread(self._sync_update, pid, **fields)

    async def adelete(self, pid: str) -> None:
        return await asyncio.to_thread(self._sync_delete, pid)
