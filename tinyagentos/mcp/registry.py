from __future__ import annotations

import json
import time

from tinyagentos.base_store import BaseStore

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    installed_at INTEGER NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    transport TEXT NOT NULL,
    running INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    last_started_at INTEGER,
    last_exit_code INTEGER,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS mcp_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL,
    scope_id TEXT,
    allowed_tools TEXT NOT NULL DEFAULT '[]',
    allowed_resources TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    UNIQUE(server_id, scope_kind, scope_id)
);
"""


class MCPServerStore(BaseStore):
    SCHEMA = _SCHEMA

    async def _post_init(self) -> None:
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.commit()

    async def list_servers(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, version, installed_at, config, transport, running, pid, "
            "last_started_at, last_exit_code, last_error FROM mcp_servers ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_server(r) for r in rows]

    async def get_server(self, server_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, version, installed_at, config, transport, running, pid, "
            "last_started_at, last_exit_code, last_error FROM mcp_servers WHERE id = ?",
            (server_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_server(row) if row else None

    async def register_server(
        self,
        server_id: str,
        version: str,
        transport: str,
        config: dict | None = None,
    ) -> None:
        now = int(time.time())
        await self._db.execute(
            "INSERT OR REPLACE INTO mcp_servers "
            "(id, version, installed_at, config, transport, running) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (server_id, version, now, json.dumps(config or {}), transport),
        )
        await self._db.commit()

    async def mark_running(self, server_id: str, pid: int) -> None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE mcp_servers SET running = 1, pid = ?, last_started_at = ?, "
            "last_exit_code = NULL, last_error = NULL WHERE id = ?",
            (pid, now, server_id),
        )
        await self._db.commit()

    async def mark_stopped(
        self,
        server_id: str,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE mcp_servers SET running = 0, pid = NULL, "
            "last_exit_code = ?, last_error = ? WHERE id = ?",
            (exit_code, error, server_id),
        )
        await self._db.commit()

    async def delete_server(self, server_id: str) -> None:
        await self._db.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        await self._db.commit()

    async def set_config(self, server_id: str, config: dict) -> None:
        await self._db.execute(
            "UPDATE mcp_servers SET config = ? WHERE id = ?",
            (json.dumps(config), server_id),
        )
        await self._db.commit()

    async def get_config(self, server_id: str) -> dict:
        async with self._db.execute(
            "SELECT config FROM mcp_servers WHERE id = ?", (server_id,)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else {}

    # --- Attachment methods ---

    async def list_attachments(self, server_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT id, server_id, scope_kind, scope_id, allowed_tools, "
            "allowed_resources, created_at FROM mcp_attachments WHERE server_id = ? ORDER BY id",
            (server_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_attachment(r) for r in rows]

    async def add_attachment(
        self,
        server_id: str,
        scope_kind: str,
        scope_id: str | None,
        allowed_tools: list[str] | None = None,
        allowed_resources: list[str] | None = None,
    ) -> int:
        now = int(time.time())
        cur = await self._db.execute(
            "INSERT INTO mcp_attachments "
            "(server_id, scope_kind, scope_id, allowed_tools, allowed_resources, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                server_id,
                scope_kind,
                scope_id,
                json.dumps(allowed_tools or []),
                json.dumps(allowed_resources or []),
                now,
            ),
        )
        await self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def delete_attachment(self, attachment_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM mcp_attachments WHERE id = ?", (attachment_id,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def list_attachments_for_agent(
        self, agent_name: str, group_names: list[str]
    ) -> list[dict]:
        placeholders = ",".join("?" for _ in group_names)
        group_ids = [f"group:{g}" for g in group_names]
        params: list = [agent_name] + group_ids
        group_clause = f"scope_id IN ({placeholders})" if group_names else "0"

        sql = (
            "SELECT id, server_id, scope_kind, scope_id, allowed_tools, "
            "allowed_resources, created_at FROM mcp_attachments WHERE "
            "scope_kind = 'all' "
            "OR (scope_kind = 'agent' AND scope_id = ?) "
        )
        if group_names:
            sql += f"OR (scope_kind = 'group' AND scope_id IN ({','.join('?' for _ in group_names)}))"
            params = [agent_name] + group_names
        else:
            params = [agent_name]

        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_attachment(r) for r in rows]


def _row_to_server(row: tuple) -> dict:
    return {
        "id": row[0],
        "version": row[1],
        "installed_at": row[2],
        "config": json.loads(row[3]),
        "transport": row[4],
        "running": bool(row[5]),
        "pid": row[6],
        "last_started_at": row[7],
        "last_exit_code": row[8],
        "last_error": row[9],
    }


def _row_to_attachment(row: tuple) -> dict:
    return {
        "id": row[0],
        "server_id": row[1],
        "scope_kind": row[2],
        "scope_id": row[3],
        "allowed_tools": json.loads(row[4]),
        "allowed_resources": json.loads(row[5]),
        "created_at": row[6],
    }
