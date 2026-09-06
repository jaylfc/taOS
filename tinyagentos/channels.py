from __future__ import annotations

import json
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

CHANNELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    type TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    UNIQUE(agent_name, type)
);
"""

CHANNEL_TYPES = {
    "web-chat": {
        "name": "Web Chat",
        "difficulty": "easy",
        "description": "Built-in chat in TinyAgentOS UI \u2014 zero config",
        "config_fields": [],
    },
    "telegram": {
        "name": "Telegram",
        "difficulty": "easy",
        "description": "Telegram bot \u2014 just need a token from @BotFather (30 seconds)",
        "config_fields": [
            {"name": "bot_token_secret", "label": "Bot Token (from Secrets)", "type": "secret-select", "required": True},
        ],
    },
    "email": {
        "name": "Email",
        "difficulty": "easy",
        "description": "Send/receive email \u2014 SMTP/IMAP",
        "config_fields": [
            {"name": "smtp_host", "label": "SMTP Host", "type": "text", "required": True},
            {"name": "smtp_port", "label": "SMTP Port", "type": "number", "required": True, "default": 587},
            {"name": "imap_host", "label": "IMAP Host", "type": "text", "required": True},
            {"name": "email_secret", "label": "Email Credentials (from Secrets)", "type": "secret-select", "required": True},
        ],
    },
    "discord": {
        "name": "Discord",
        "difficulty": "advanced",
        "description": "Discord bot \u2014 needs Discord Developer Application",
        "config_fields": [
            {"name": "bot_token_secret", "label": "Bot Token (from Secrets)", "type": "secret-select", "required": True},
            {"name": "guild_id", "label": "Server/Guild ID", "type": "text", "required": False},
        ],
    },
    "slack": {
        "name": "Slack",
        "difficulty": "advanced",
        "description": "Slack app \u2014 needs Slack API app creation",
        "config_fields": [
            {"name": "bot_token_secret", "label": "Bot Token (from Secrets)", "type": "secret-select", "required": True},
            {"name": "signing_secret", "label": "Signing Secret (from Secrets)", "type": "secret-select", "required": True},
        ],
    },
    "signal": {
        "name": "Signal",
        "difficulty": "advanced",
        "description": "Signal messenger \u2014 needs Signal CLI setup",
        "config_fields": [
            {"name": "phone_number", "label": "Phone Number", "type": "text", "required": True},
        ],
    },
    "matrix": {
        "name": "Matrix",
        "difficulty": "advanced",
        "description": "Matrix chat \u2014 needs homeserver access",
        "config_fields": [
            {"name": "homeserver", "label": "Homeserver URL", "type": "url", "required": True},
            {"name": "access_token_secret", "label": "Access Token (from Secrets)", "type": "secret-select", "required": True},
        ],
    },
    "webhook": {
        "name": "Webhook",
        "difficulty": "advanced",
        "description": "Generic HTTP webhook \u2014 incoming and outgoing",
        "config_fields": [
            {"name": "url", "label": "Webhook URL", "type": "url", "required": True},
            {"name": "auth_secret", "label": "Auth Header (from Secrets)", "type": "secret-select", "required": False},
        ],
    },
}


class ChannelStore(BaseStore):
    """SQLite-backed store for agent communication channel configurations."""

    SCHEMA = CHANNELS_SCHEMA

    async def add(self, agent_name: str, channel_type: str, config: dict | None = None) -> int:
        """Add or replace a channel for an agent. Returns the row id."""
        now = int(time.time())
        cursor = await self._db.execute(
            "INSERT OR REPLACE INTO channels (agent_name, type, config, created_at) VALUES (?, ?, ?, ?)",
            (agent_name, channel_type, json.dumps(config or {}), now),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def list_for_agent(self, agent_name: str) -> list[dict]:
        """List channels configured for a specific agent."""
        async with self._db.execute(
            "SELECT id, type, config, enabled FROM channels WHERE agent_name = ? ORDER BY type",
            (agent_name,),
        ) as cursor:
            return [
                {
                    "id": r[0],
                    "type": r[1],
                    "config": json.loads(r[2]),
                    "enabled": bool(r[3]),
                    **CHANNEL_TYPES.get(r[1], {}),
                }
                for r in await cursor.fetchall()
            ]

    async def list_all(self) -> list[dict]:
        """List all channels across all agents."""
        async with self._db.execute(
            "SELECT id, agent_name, type, config, enabled FROM channels ORDER BY agent_name, type"
        ) as cursor:
            return [
                {
                    "id": r[0],
                    "agent_name": r[1],
                    "type": r[2],
                    "config": json.loads(r[3]),
                    "enabled": bool(r[4]),
                    **CHANNEL_TYPES.get(r[2], {}),
                }
                for r in await cursor.fetchall()
            ]

    async def get(self, channel_id: int) -> dict | None:
        """Get a single channel by ID."""
        async with self._db.execute(
            "SELECT id, agent_name, type, config, enabled FROM channels WHERE id = ?",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "agent_name": row[1],
            "type": row[2],
            "config": json.loads(row[3]),
            "enabled": bool(row[4]),
            **CHANNEL_TYPES.get(row[2], {}),
        }

    async def update(self, channel_id: int, config: dict) -> None:
        """Update a channel's config JSON."""
        await self._db.execute(
            "UPDATE channels SET config = ? WHERE id = ?",
            (json.dumps(config), channel_id),
        )
        await self._db.commit()

    async def remove(self, agent_name: str, channel_type: str) -> bool:
        """Remove a channel. Returns True if a row was deleted."""
        cursor = await self._db.execute(
            "DELETE FROM channels WHERE agent_name = ? AND type = ?",
            (agent_name, channel_type),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def toggle(self, channel_id: int, enabled: bool) -> None:
        """Enable or disable a channel by id."""
        await self._db.execute(
            "UPDATE channels SET enabled = ? WHERE id = ?",
            (int(enabled), channel_id),
        )
        await self._db.commit()
