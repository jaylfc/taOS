from __future__ import annotations

import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

RELATIONSHIPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    lead_agent TEXT,
    color TEXT DEFAULT '#888888',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    role TEXT DEFAULT 'member',
    PRIMARY KEY (group_id, agent_name),
    FOREIGN KEY (group_id) REFERENCES agent_groups(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS agent_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT 'message',
    UNIQUE(from_agent, to_agent, permission)
);
"""


class RelationshipManager(BaseStore):
    SCHEMA = RELATIONSHIPS_SCHEMA

    async def _post_init(self) -> None:
        await self._db.execute("PRAGMA foreign_keys = ON")

    # Groups
    async def create_group(
        self,
        name: str,
        description: str = "",
        lead_agent: str | None = None,
        color: str = "#888888",
    ) -> int:
        now = int(time.time())
        cursor = await self._db.execute(
            "INSERT INTO agent_groups (name, description, lead_agent, color, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, description, lead_agent, color, now),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def list_groups(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, name, description, lead_agent, color FROM agent_groups ORDER BY name"
        ) as cursor:
            groups = []
            for r in await cursor.fetchall():
                members = await self._get_group_members(r[0])
                groups.append({
                    "id": r[0],
                    "name": r[1],
                    "description": r[2],
                    "lead_agent": r[3],
                    "color": r[4],
                    "members": members,
                })
            return groups

    async def delete_group(self, group_id: int) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM agent_groups WHERE id = ?", (group_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def update_group(self, group_id: int, **kwargs):
        for field in ["name", "description", "lead_agent", "color"]:
            if field in kwargs:
                await self._db.execute(
                    f"UPDATE agent_groups SET {field} = ? WHERE id = ?",
                    (kwargs[field], group_id),
                )
        await self._db.commit()

    # Members
    async def add_member(
        self, group_id: int, agent_name: str, role: str = "member"
    ):
        await self._db.execute(
            "INSERT OR REPLACE INTO group_members (group_id, agent_name, role) VALUES (?, ?, ?)",
            (group_id, agent_name, role),
        )
        await self._db.commit()

    async def remove_member(self, group_id: int, agent_name: str):
        await self._db.execute(
            "DELETE FROM group_members WHERE group_id = ? AND agent_name = ?",
            (group_id, agent_name),
        )
        await self._db.commit()

    async def get_agent_groups(self, agent_name: str) -> list[dict]:
        async with self._db.execute(
            """
            SELECT g.id, g.name, g.description, g.lead_agent, g.color, gm.role
            FROM agent_groups g JOIN group_members gm ON gm.group_id = g.id
            WHERE gm.agent_name = ?
            """,
            (agent_name,),
        ) as cursor:
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "description": r[2],
                    "lead_agent": r[3],
                    "color": r[4],
                    "role": r[5],
                }
                for r in await cursor.fetchall()
            ]

    async def _get_group_members(self, group_id: int) -> list[dict]:
        async with self._db.execute(
            "SELECT agent_name, role FROM group_members WHERE group_id = ?",
            (group_id,),
        ) as cursor:
            return [
                {"agent_name": r[0], "role": r[1]}
                for r in await cursor.fetchall()
            ]

    # Permissions
    async def set_permission(
        self, from_agent: str, to_agent: str, permission: str = "message"
    ):
        await self._db.execute(
            "INSERT OR IGNORE INTO agent_permissions (from_agent, to_agent, permission) VALUES (?, ?, ?)",
            (from_agent, to_agent, permission),
        )
        await self._db.commit()

    async def revoke_permission(
        self, from_agent: str, to_agent: str, permission: str = "message"
    ):
        await self._db.execute(
            "DELETE FROM agent_permissions WHERE from_agent = ? AND to_agent = ? AND permission = ?",
            (from_agent, to_agent, permission),
        )
        await self._db.commit()

    async def can_communicate(self, from_agent: str, to_agent: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM agent_permissions WHERE from_agent = ? AND to_agent = ? AND permission = 'message'",
            (from_agent, to_agent),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get_agent_permissions(self, agent_name: str) -> dict:
        can_reach = []
        async with self._db.execute(
            "SELECT to_agent FROM agent_permissions WHERE from_agent = ?",
            (agent_name,),
        ) as cursor:
            can_reach = [r[0] for r in await cursor.fetchall()]
        reachable_by = []
        async with self._db.execute(
            "SELECT from_agent FROM agent_permissions WHERE to_agent = ?",
            (agent_name,),
        ) as cursor:
            reachable_by = [r[0] for r in await cursor.fetchall()]
        return {"can_reach": can_reach, "reachable_by": reachable_by}
