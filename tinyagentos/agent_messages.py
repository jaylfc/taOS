from __future__ import annotations

import json
import time

from tinyagentos.base_store import BaseStore

MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    message TEXT NOT NULL,
    tool_calls TEXT DEFAULT '[]',
    tool_results TEXT DEFAULT '[]',
    reasoning TEXT DEFAULT '',
    depth INTEGER NOT NULL DEFAULT 2,
    metadata TEXT DEFAULT '{}',
    timestamp REAL NOT NULL,
    read INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_from ON agent_messages(from_agent, timestamp);
CREATE INDEX IF NOT EXISTS idx_msg_to ON agent_messages(to_agent, timestamp);
"""


class AgentMessageStore(BaseStore):
    SCHEMA = MESSAGES_SCHEMA

    async def send(self, from_agent: str, to_agent: str, message: str,
                   tool_calls: list | None = None, tool_results: list | None = None,
                   reasoning: str = "", depth: int = 2,
                   metadata: dict | None = None) -> int:
        now = time.time()
        cursor = await self._db.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message, tool_calls, tool_results, reasoning, depth, metadata, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (from_agent, to_agent, message, json.dumps(tool_calls or []),
             json.dumps(tool_results or []), reasoning or "", depth,
             json.dumps(metadata or {}), now))
        await self._db.commit()
        return cursor.lastrowid

    async def get_messages(self, agent_name: str, limit: int = 50,
                           depth: int = 2) -> list[dict]:
        """Get all messages involving an agent (sent or received).

        depth controls transcript detail level:
          1 = message text only
          2 = message + tool calls/results (default)
          3 = message + tool calls + reasoning
        """
        async with self._db.execute(
            """SELECT id, from_agent, to_agent, message, tool_calls, tool_results,
                      reasoning, depth, metadata, timestamp, read
               FROM agent_messages WHERE from_agent = ? OR to_agent = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (agent_name, agent_name, limit)
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._format_message(r, depth) for r in rows]

    @staticmethod
    def _format_message(r: tuple, view_depth: int = 2) -> dict:
        """Format a message row, filtering fields by view depth."""
        msg = {
            "id": r[0], "from": r[1], "to": r[2], "message": r[3],
            "metadata": json.loads(r[8]), "timestamp": r[9], "read": bool(r[10]),
            "depth": r[7],
        }
        if view_depth >= 2:
            msg["tool_calls"] = json.loads(r[4])
            msg["tool_results"] = json.loads(r[5])
        else:
            msg["tool_calls"] = []
            msg["tool_results"] = []
        if view_depth >= 3:
            msg["reasoning"] = r[6]
        else:
            msg["reasoning"] = ""
        return msg

    async def get_conversation(self, agent1: str, agent2: str, limit: int = 50,
                               depth: int = 2) -> list[dict]:
        """Get messages between two specific agents."""
        async with self._db.execute(
            """SELECT id, from_agent, to_agent, message, tool_calls, tool_results,
                      reasoning, depth, metadata, timestamp, read
               FROM agent_messages
               WHERE (from_agent = ? AND to_agent = ?) OR (from_agent = ? AND to_agent = ?)
               ORDER BY timestamp ASC LIMIT ?""",
            (agent1, agent2, agent2, agent1, limit)
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._format_message(r, depth) for r in rows]

    async def get_contacts(self, agent_name: str) -> list[dict]:
        """Get list of agents this agent has communicated with + unread counts."""
        async with self._db.execute(
            """SELECT partner, SUM(unread) as unread_count FROM (
                SELECT from_agent as partner, SUM(CASE WHEN read = 0 THEN 1 ELSE 0 END) as unread
                FROM agent_messages WHERE to_agent = ?
                GROUP BY from_agent
                UNION ALL
                SELECT to_agent as partner, 0 as unread
                FROM agent_messages WHERE from_agent = ?
                GROUP BY to_agent
            ) GROUP BY partner ORDER BY partner""",
            (agent_name, agent_name)
        ) as cursor:
            rows = await cursor.fetchall()
        return [{"name": r[0], "unread_count": r[1]} for r in rows]

    async def mark_read(self, agent_name: str):
        await self._db.execute("UPDATE agent_messages SET read = 1 WHERE to_agent = ?", (agent_name,))
        await self._db.commit()

    async def unread_count(self, agent_name: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE to_agent = ? AND read = 0", (agent_name,)
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def delete(self, message_id: int) -> bool:
        """Delete a single message by ID. Returns True if deleted."""
        cursor = await self._db.execute(
            "DELETE FROM agent_messages WHERE id = ?", (message_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def search(self, query: str, agent_name: str | None = None,
                     limit: int = 50) -> list[dict]:
        """Search messages by content. Optionally filter by agent."""
        pattern = f"%{query}%"
        if agent_name:
            sql = """SELECT id, from_agent, to_agent, message, tool_calls, tool_results,
                            reasoning, depth, metadata, timestamp, read
                     FROM agent_messages
                     WHERE (from_agent = ? OR to_agent = ?) AND message LIKE ?
                     ORDER BY timestamp DESC LIMIT ?"""
            params = (agent_name, agent_name, pattern, limit)
        else:
            sql = """SELECT id, from_agent, to_agent, message, tool_calls, tool_results,
                            reasoning, depth, metadata, timestamp, read
                     FROM agent_messages
                     WHERE message LIKE ?
                     ORDER BY timestamp DESC LIMIT ?"""
            params = (pattern, limit)
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._format_message(r) for r in rows]
