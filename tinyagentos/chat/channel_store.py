from __future__ import annotations

import json
import time
import uuid

from tinyagentos.base_store import BaseStore

CHANNELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    members TEXT NOT NULL DEFAULT '[]',
    settings TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_message_at REAL,
    project_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chat_read_positions (
    user_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    last_read_message_id TEXT NOT NULL,
    last_read_at REAL NOT NULL,
    PRIMARY KEY (user_id, channel_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    thread_id TEXT,
    author_id TEXT NOT NULL,
    author_type TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'text',
    content_blocks TEXT NOT NULL DEFAULT '[]',
    embeds TEXT NOT NULL DEFAULT '[]',
    components TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]',
    reactions TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'complete',
    edited_at REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    ephemeral INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_channel ON chat_messages(channel_id, created_at);
"""

PHASE1_DEFAULT_SETTINGS = {
    "response_mode": "quiet",
    "max_hops": 3,
    "cooldown_seconds": 5,
    "rate_cap_per_minute": 20,
    "muted": [],
}

_CHANNEL_JSON_FIELDS = ("members", "settings")


def _parse_channel(row: tuple, description) -> dict:
    keys = [d[0] for d in description]
    ch = dict(zip(keys, row))
    for field in _CHANNEL_JSON_FIELDS:
        if field in ch and ch[field] is not None:
            ch[field] = json.loads(ch[field])
    settings = ch.get("settings") or {}
    for key, default in PHASE1_DEFAULT_SETTINGS.items():
        settings.setdefault(key, default if not isinstance(default, list) else list(default))
    ch["settings"] = settings
    return ch


class ChatChannelStore(BaseStore):
    SCHEMA = CHANNELS_SCHEMA

    async def _post_init(self) -> None:
        async with self._db.execute("PRAGMA table_info(chat_channels)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
        if "project_id" not in cols:
            await self._db.execute(
                "ALTER TABLE chat_channels ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
            )
            await self._db.commit()

    async def create_channel(
        self,
        name: str,
        type: str,
        created_by: str,
        members: list | None = None,
        description: str = "",
        topic: str = "",
        settings: dict | None = None,
        project_id: str = "",
    ) -> dict:
        ch_id = uuid.uuid4().hex[:12]
        now = time.time()
        await self._db.execute(
            """INSERT INTO chat_channels
               (id, name, type, description, topic, members, settings, created_by, created_at, last_message_at, project_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                ch_id, name, type, description, topic,
                json.dumps(members or []),
                json.dumps(settings or {}),
                created_by, now, project_id,
            ),
        )
        await self._db.commit()
        return await self.get_channel(ch_id)

    async def get_channel(self, channel_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM chat_channels WHERE id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return _parse_channel(row, cursor.description)

    async def list_channels(
        self,
        member_id: str | None = None,
        archived: bool | None = None,
        project_id: str | None = None,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list = []

        if member_id is not None:
            conditions.append("members LIKE ?")
            params.append(f'%"{member_id}"%')

        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)

        # archived filter — use JSON text search for speed (settings is stored as
        # a JSON string; the canonical marker is '"archived": true').
        if archived is True:
            conditions.append('settings LIKE \'%"archived": true%\'')
        elif archived is False:
            conditions.append('(settings NOT LIKE \'%"archived": true%\')')

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM chat_channels {where} ORDER BY created_at ASC"
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            desc = cursor.description
        return [_parse_channel(r, desc) for r in rows]

    async def update_channel(
        self,
        channel_id: str,
        name: str | None = None,
        description: str | None = None,
        topic: str | None = None,
        settings: dict | None = None,
    ) -> None:
        sets = []
        params = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if topic is not None:
            sets.append("topic = ?")
            params.append(topic)
        if settings is not None:
            sets.append("settings = ?")
            params.append(json.dumps(settings))
        if not sets:
            return
        params.append(channel_id)
        await self._db.execute(
            f"UPDATE chat_channels SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()

    async def set_project(self, channel_id: str, project_id: str) -> None:
        await self._db.execute(
            "UPDATE chat_channels SET project_id = ? WHERE id = ?",
            (project_id, channel_id),
        )
        await self._db.commit()

    async def set_settings(self, channel_id: str, updates: dict) -> None:
        """Merge ``updates`` into the channel's settings dict, then persist.

        Existing keys not present in ``updates`` are preserved.  Pass
        ``{key: False}`` (or any falsy value) to clear an individual key
        without touching others.
        """
        async with self._db.execute(
            "SELECT settings FROM chat_channels WHERE id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return
        current: dict = json.loads(row[0]) if row[0] else {}
        current.update(updates)
        await self._db.execute(
            "UPDATE chat_channels SET settings = ? WHERE id = ?",
            (json.dumps(current), channel_id),
        )
        await self._db.commit()

    async def set_response_mode(self, channel_id: str, mode: str) -> None:
        if mode not in ("quiet", "lively"):
            raise ValueError(f"invalid response_mode: {mode}")
        await self.set_settings(channel_id, {"response_mode": mode})

    async def set_max_hops(self, channel_id: str, hops: int) -> None:
        if not 1 <= hops <= 10:
            raise ValueError("max_hops must be 1..10")
        await self.set_settings(channel_id, {"max_hops": hops})

    async def set_cooldown_seconds(self, channel_id: str, seconds: int) -> None:
        if not 0 <= seconds <= 60:
            raise ValueError("cooldown_seconds must be 0..60")
        await self.set_settings(channel_id, {"cooldown_seconds": seconds})

    async def set_ephemeral_ttl(self, channel_id: str, seconds: int | None) -> None:
        """Set or clear the channel's ephemeral TTL. None = disabled."""
        if seconds is not None:
            if not isinstance(seconds, (int, float)) or seconds < 0:
                raise ValueError("ephemeral_ttl_seconds must be a non-negative number or null")
            if seconds > 30 * 24 * 60 * 60:
                raise ValueError("ephemeral_ttl_seconds must be <= 30 days")
        await self.set_settings(channel_id, {"ephemeral_ttl_seconds": seconds})

    async def mute_agent(self, channel_id: str, slug: str) -> None:
        ch = await self.get_channel(channel_id)
        if ch is None:
            return
        muted = set(ch["settings"].get("muted") or [])
        muted.add(slug)
        await self.set_settings(channel_id, {"muted": sorted(muted)})

    async def unmute_agent(self, channel_id: str, slug: str) -> None:
        ch = await self.get_channel(channel_id)
        if ch is None:
            return
        muted = set(ch["settings"].get("muted") or [])
        muted.discard(slug)
        await self.set_settings(channel_id, {"muted": sorted(muted)})

    async def delete_channel(self, channel_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM chat_channels WHERE id = ?", (channel_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def add_member(self, channel_id: str, member_id: str) -> None:
        async with self._db.execute(
            "SELECT members FROM chat_channels WHERE id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return
        members = json.loads(row[0])
        if member_id not in members:
            members.append(member_id)
        await self._db.execute(
            "UPDATE chat_channels SET members = ? WHERE id = ?",
            (json.dumps(members), channel_id),
        )
        await self._db.commit()

    async def remove_member(self, channel_id: str, member_id: str) -> None:
        async with self._db.execute(
            "SELECT members FROM chat_channels WHERE id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return
        members = json.loads(row[0])
        members = [m for m in members if m != member_id]
        await self._db.execute(
            "UPDATE chat_channels SET members = ? WHERE id = ?",
            (json.dumps(members), channel_id),
        )
        await self._db.commit()

    async def update_read_position(
        self, user_id: str, channel_id: str, message_id: str
    ) -> None:
        now = time.time()
        await self._db.execute(
            """INSERT OR REPLACE INTO chat_read_positions
               (user_id, channel_id, last_read_message_id, last_read_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, channel_id, message_id, now),
        )
        await self._db.commit()

    async def rewind_read_cursor(self, user_id: str, channel_id: str, before_ts: float) -> None:
        """Set last_read_at for this (user, channel) to before_ts.

        Used for mark-unread: caller passes msg.created_at - epsilon so
        everything from that message forward counts as unread.
        Creates the row if it doesn't exist yet.
        """
        await self._db.execute(
            """INSERT OR REPLACE INTO chat_read_positions
               (user_id, channel_id, last_read_message_id, last_read_at)
               VALUES (?, ?, '', ?)""",
            (user_id, channel_id, before_ts),
        )
        await self._db.commit()

    async def get_unread_counts(self, user_id: str) -> dict[str, int]:
        # Get all channels where the user is a member
        pattern = f'%"{user_id}"%'
        async with self._db.execute(
            "SELECT id FROM chat_channels WHERE members LIKE ?", (pattern,)
        ) as cursor:
            channel_rows = await cursor.fetchall()
        channel_ids = [r[0] for r in channel_rows]
        if not channel_ids:
            return {}

        result: dict[str, int] = {}
        for ch_id in channel_ids:
            # Get user's last read time for this channel
            async with self._db.execute(
                "SELECT last_read_at FROM chat_read_positions WHERE user_id = ? AND channel_id = ?",
                (user_id, ch_id),
            ) as cursor:
                pos_row = await cursor.fetchone()
            last_read_at = pos_row[0] if pos_row else 0.0

            async with self._db.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE channel_id = ? AND created_at > ?",
                (ch_id, last_read_at),
            ) as cursor:
                count_row = await cursor.fetchone()
            result[ch_id] = count_row[0] if count_row else 0
        return result

    async def update_last_message_at(self, channel_id: str) -> None:
        now = time.time()
        await self._db.execute(
            "UPDATE chat_channels SET last_message_at = ? WHERE id = ?", (now, channel_id)
        )
        await self._db.commit()
