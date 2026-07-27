from __future__ import annotations

import asyncio
import json
import time
import uuid

from tinyagentos.base_store import BaseStore

MESSAGES_SCHEMA = """
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
    deleted_at REAL,
    expires_at REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    ephemeral INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_channel ON chat_messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id);

CREATE TABLE IF NOT EXISTS chat_pins (
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    pinned_by TEXT NOT NULL,
    pinned_at REAL NOT NULL,
    PRIMARY KEY (channel_id, message_id)
);

CREATE TABLE IF NOT EXISTS chat_attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    path TEXT NOT NULL,
    thumbnail_path TEXT,
    uploaded_by TEXT NOT NULL,
    uploaded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_message ON chat_attachments(message_id);
"""

_JSON_FIELDS = ("content_blocks", "embeds", "components", "attachments", "reactions", "metadata")

PIN_CAP_PER_CHANNEL = 50


def _parse(row: tuple, description) -> dict:
    keys = [d[0] for d in description]
    msg = dict(zip(keys, row))
    for field in _JSON_FIELDS:
        if field in msg and msg[field] is not None:
            msg[field] = json.loads(msg[field])
    if msg.get("metadata") is None:
        msg["metadata"] = {}
    return msg


class ChatMessageStore(BaseStore):
    SCHEMA = MESSAGES_SCHEMA

    def __init__(self, db_path) -> None:
        super().__init__(db_path)
        # Serialises concurrent reaction read-modify-write operations
        self._reaction_lock = asyncio.Lock()

    async def init(self) -> None:
        await super().init()
        try:
            await self._db.execute("ALTER TABLE chat_messages ADD COLUMN deleted_at REAL")
            await self._db.commit()
        except Exception:
            # column already exists (SQLite raises on duplicate column name)
            pass
        try:
            await self._db.execute("ALTER TABLE chat_messages ADD COLUMN expires_at REAL")
            await self._db.commit()
        except Exception:
            pass
        # E1 collab: remote message dedup columns
        try:
            await self._db.execute("ALTER TABLE chat_messages ADD COLUMN remote_msg_id TEXT")
            await self._db.commit()
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE chat_messages ADD COLUMN delivered_at REAL")
            await self._db.commit()
        except Exception:
            pass
        # Dedup index MUST be created here (not in SCHEMA) because it references
        # columns added by _post_init — per BaseStore boot-brick rule.
        try:
            await self._db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_remote_dedup "
                "ON chat_messages(channel_id, remote_msg_id) "
                "WHERE remote_msg_id IS NOT NULL"
            )
            await self._db.commit()
        except Exception:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning("chat_messages: could not create remote dedup index — "
                         "pre-existing data may violate uniqueness constraint")

    async def send_message(
        self,
        channel_id: str,
        author_id: str,
        author_type: str,
        content: str,
        content_type: str = "text",
        thread_id: str | None = None,
        embeds: list | None = None,
        components: list | None = None,
        attachments: list | None = None,
        content_blocks: list | None = None,
        metadata: dict | None = None,
        state: str = "complete",
        expires_at: float | None = None,
    ) -> dict:
        msg_id = uuid.uuid4().hex[:12]
        now = time.time()
        await self._db.execute(
            """INSERT INTO chat_messages
               (id, channel_id, thread_id, author_id, author_type, content,
                content_type, content_blocks, embeds, components, attachments,
                reactions, state, pinned, ephemeral, metadata, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
            (
                msg_id, channel_id, thread_id, author_id, author_type, content,
                content_type,
                json.dumps(content_blocks or []),
                json.dumps(embeds or []),
                json.dumps(components or []),
                json.dumps(attachments or []),
                json.dumps({}),
                state,
                json.dumps(metadata or {}),
                now,
                expires_at,
            ),
        )
        await self._db.commit()
        return await self.get_message(msg_id)

    async def send_remote_message(
        self,
        channel_id: str,
        author_id: str,
        contact_id: str,
        remote_msg_id: str,
        content: str,
        *,
        content_type: str = "text",
        thread_id: str | None = None,
        content_blocks: list | None = None,
        metadata: dict | None = None,
        created_at: float | None = None,
    ) -> dict | None:
        """Insert a DM received from a remote contact, deduplicating on
        ``(channel_id, remote_msg_id)``.

        Returns the message dict on success, or ``None`` when the message
        was already delivered (duplicate).  ``author_id`` is namespaced as
        ``hub:{username}`` per the collab spec.

        The caller MUST commit the message before sending the delivery ack.
        """
        import sqlite3

        if not remote_msg_id or not isinstance(remote_msg_id, str):
            raise ValueError("remote_msg_id must be a non-empty string")

        msg_id = uuid.uuid4().hex[:12]
        now = created_at if created_at is not None else time.time()

        # Stash the contact_id in metadata so downstream consumers can
        # resolve the sender without parsing author_id.
        merged_meta = dict(metadata or {})
        merged_meta.setdefault("contact_id", contact_id)

        try:
            await self._db.execute(
                """INSERT INTO chat_messages
                   (id, channel_id, thread_id, author_id, author_type, content,
                    content_type, content_blocks, embeds, components, attachments,
                    reactions, state, pinned, ephemeral, metadata, created_at,
                    remote_msg_id, delivered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)""",
                (
                    msg_id, channel_id, thread_id, author_id, "user", content,
                    content_type,
                    json.dumps(content_blocks or []),
                    json.dumps([]),  # embeds
                    json.dumps([]),  # components
                    json.dumps([]),  # attachments
                    json.dumps({}),  # reactions
                    "complete",
                    json.dumps(merged_meta),
                    now,
                    remote_msg_id,
                    time.time(),  # delivered_at — actual receipt time, not sender's created_at
                ),
            )
            await self._db.commit()
            return await self.get_message(msg_id)
        except sqlite3.IntegrityError as exc:
            # Re-raise on structural integrity violations (e.g. NOT NULL on
            # channel_id) — only the dedup index violation is benign.
            if "UNIQUE" not in str(exc) or "remote_msg_id" not in str(exc):
                await self._db.rollback()
                raise
            # Duplicate — the (channel_id, remote_msg_id) unique constraint
            # caught a replay.  Treat as successful delivery.
            await self._db.rollback()
            return None

    async def get_message(self, message_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM chat_messages WHERE id = ?", (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return _parse(row, cursor.description)

    async def get_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: float | None = None,
        after: float | None = None,
    ) -> list[dict]:
        params: list = [channel_id]
        where = "channel_id = ?"
        if before is not None:
            where += " AND created_at < ?"
            params.append(before)
        if after is not None:
            where += " AND created_at > ?"
            params.append(after)
        params.append(limit)
        sql = f"""
            SELECT * FROM (
                SELECT * FROM chat_messages WHERE {where}
                ORDER BY created_at DESC LIMIT ?
            ) ORDER BY created_at ASC
        """
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            desc = cursor.description
        return [_parse(r, desc) for r in rows]

    async def edit_message(self, message_id: str, content: str) -> None:
        now = time.time()
        await self._db.execute(
            "UPDATE chat_messages SET content = ?, edited_at = ? WHERE id = ?",
            (content, now, message_id),
        )
        await self._db.commit()

    async def set_metadata(self, message_id: str, metadata: dict) -> None:
        await self._db.execute(
            "UPDATE chat_messages SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), message_id),
        )
        await self._db.commit()

    async def soft_delete_message(self, message_id: str) -> bool:
        """Mark message as soft-deleted; returns True if a row was updated."""
        now = time.time()
        cursor = await self._db.execute(
            "UPDATE chat_messages SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, message_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_message(self, message_id: str) -> bool:
        """Canonical delete = soft delete (Phase 2b-2a)."""
        return await self.soft_delete_message(message_id)

    async def soft_delete_messages_after(
        self, channel_id: str, after_timestamp: float,
        thread_id: str | None = None,
    ) -> list[str]:
        """Soft-delete all non-deleted messages in *channel_id* whose
        ``created_at`` is strictly greater than *after_timestamp*.
        When *thread_id* is given, only messages within that thread are
        deleted; when it is ``None``, only main-timeline messages (those
        with ``thread_id IS NULL``) are deleted.
        Returns the list of affected message ids so callers can broadcast
        per-message delete events."""
        now = time.time()
        if thread_id is not None:
            thread_clause = "AND thread_id = ?"
            params = (now, channel_id, after_timestamp, thread_id)
        else:
            thread_clause = "AND thread_id IS NULL"
            params = (now, channel_id, after_timestamp)
        # NOTE: strict > means a message sharing the edited message's exact
        # time.time() survives — at most one stale adjacent message can
        # slip through a timestamp tie.
        cursor = await self._db.execute(
            f"""UPDATE chat_messages SET deleted_at = ?
               WHERE channel_id = ? AND created_at > ? AND deleted_at IS NULL
               {thread_clause}
               RETURNING id""",
            params,
        )
        rows = await cursor.fetchall()
        await self._db.commit()
        return [r[0] for r in rows]

    async def edit_and_truncate_branch(
        self,
        message_id: str,
        content: str,
        *,
        channel_type: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[dict | None, list[str]]:
        """Edit a message and, if *channel_type* is ``"dm"``, soft-delete
        every subsequent message in the same channel and thread so the
        agent branch is rewritten on the next turn.

        All writes happen inside a single transaction so the edit +
        truncation are atomic.  Timestamp-tie rows are handled
        deterministically: messages with a strictly later ``created_at``
        are deleted; messages sharing the exact same timestamp are only
        deleted when their stable ``id`` sorts after *message_id*.

        Returns ``(updated_message, deleted_ids)``.
        """
        now = time.time()
        deleted_ids: list[str] = []

        await self._db.execute("BEGIN")
        try:
            # 1. Edit the message
            await self._db.execute(
                "UPDATE chat_messages SET content = ?, edited_at = ? WHERE id = ?",
                (content, now, message_id),
            )

            # 2. Truncate subsequent messages in DM channels
            if channel_type == "dm":
                cursor = await self._db.execute(
                    "SELECT channel_id, created_at FROM chat_messages WHERE id = ?",
                    (message_id,),
                )
                row = await cursor.fetchone()
                if row is not None:
                    ch_id, created_at = row[0], row[1]
                    if thread_id is not None:
                        thread_clause = "AND thread_id = ?"
                        params = (now, ch_id, created_at, created_at,
                                  message_id, thread_id)
                    else:
                        thread_clause = "AND thread_id IS NULL"
                        params = (now, ch_id, created_at, created_at,
                                  message_id)
                    # Deterministic tie-breaking:
                    #   created_at > msg.created_at  → later → delete
                    #   created_at = msg.created_at AND id > msg.id → same time,
                    #   later row in stable-id order → delete
                    cursor = await self._db.execute(
                        f"""UPDATE chat_messages SET deleted_at = ?
                           WHERE channel_id = ? AND deleted_at IS NULL
                           AND (created_at > ? OR (created_at = ? AND id > ?))
                           {thread_clause}
                           RETURNING id""",
                        params,
                    )
                    rows = await cursor.fetchall()
                    deleted_ids = [r[0] for r in rows]

            await self._db.commit()
        except Exception:
            await self._db.execute("ROLLBACK")
            raise

        # Fetch the updated message outside the transaction
        updated = await self.get_message(message_id)
        return updated, deleted_ids

    async def add_reaction(self, message_id: str, emoji: str, user_id: str) -> None:
        async with self._reaction_lock:
            async with self._db.execute(
                "SELECT reactions FROM chat_messages WHERE id = ?", (message_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return
            reactions = json.loads(row[0])
            if emoji not in reactions:
                reactions[emoji] = []
            if user_id not in reactions[emoji]:
                reactions[emoji].append(user_id)
            await self._db.execute(
                "UPDATE chat_messages SET reactions = ? WHERE id = ?",
                (json.dumps(reactions), message_id),
            )
            await self._db.commit()

    async def remove_reaction(self, message_id: str, emoji: str, user_id: str) -> None:
        async with self._reaction_lock:
            async with self._db.execute(
                "SELECT reactions FROM chat_messages WHERE id = ?", (message_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return
            reactions = json.loads(row[0])
            if emoji in reactions:
                reactions[emoji] = [u for u in reactions[emoji] if u != user_id]
                if not reactions[emoji]:
                    del reactions[emoji]
            await self._db.execute(
                "UPDATE chat_messages SET reactions = ? WHERE id = ?",
                (json.dumps(reactions), message_id),
            )
            await self._db.commit()

    async def ensure_message(self, msg: dict) -> None:
        """Insert a message row only if its id is not already present (idempotent).

        ``msg`` must be a dict with at minimum ``id``, ``channel_id``,
        ``author_id``, ``author_type``, and ``created_at``.  All other fields
        fall back to their column defaults.
        """
        await self._db.execute(
            """INSERT OR IGNORE INTO chat_messages
               (id, channel_id, thread_id, author_id, author_type, content,
                content_type, content_blocks, embeds, components, attachments,
                reactions, state, edited_at, pinned, ephemeral, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg.get("id") or "",
                msg.get("channel_id") or "",
                msg.get("thread_id"),
                msg.get("author_id") or "",
                msg.get("author_type") or "user",
                msg.get("content") or "",
                msg.get("content_type") or "text",
                json.dumps(msg["content_blocks"]) if isinstance(msg.get("content_blocks"), list) else msg.get("content_blocks") or "[]",
                json.dumps(msg["embeds"]) if isinstance(msg.get("embeds"), list) else msg.get("embeds") or "[]",
                json.dumps(msg["components"]) if isinstance(msg.get("components"), list) else msg.get("components") or "[]",
                json.dumps(msg["attachments"]) if isinstance(msg.get("attachments"), list) else msg.get("attachments") or "[]",
                json.dumps(msg["reactions"]) if isinstance(msg.get("reactions"), dict) else msg.get("reactions") or "{}",
                msg.get("state") or "complete",
                msg.get("edited_at"),
                1 if msg.get("pinned") else 0,
                1 if msg.get("ephemeral") else 0,
                json.dumps(msg["metadata"]) if isinstance(msg.get("metadata"), dict) else msg.get("metadata") or "{}",
                msg.get("created_at") or time.time(),
            ),
        )
        await self._db.commit()

    async def get_thread_messages(
        self, channel_id: str, parent_id: str, limit: int = 20,
    ) -> list[dict]:
        """Return messages in a thread (not the parent), oldest first."""
        async with self._db.execute(
            "SELECT * FROM chat_messages "
            "WHERE channel_id = ? AND thread_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (channel_id, parent_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            description = cursor.description
        return [_parse(row, description) for row in rows]

    async def get_all_messages_for_channel(self, channel_id: str) -> list[dict]:
        """Return every message in a channel ordered by created_at ASC.

        Used for chat-export on agent archive.  Loads all rows in one query;
        for very large histories this is acceptable since export runs once at
        archive time, not on hot paths.
        """
        sql = "SELECT * FROM chat_messages WHERE channel_id = ? ORDER BY created_at ASC"
        async with self._db.execute(sql, (channel_id,)) as cursor:
            rows = await cursor.fetchall()
            desc = cursor.description
        return [_parse(r, desc) for r in rows]

    async def sweep_expired(self) -> list[tuple[str, str]]:
        """Soft-delete messages past their expires_at. Returns list of (message_id, channel_id).

        Uses chunked batch UPDATEs to stay within SQLite's variable limit (999 / 32766).
        """
        _CHUNK = 500
        now = time.time()
        # Collect the rows we are about to expire (needed for the return value)
        async with self._db.execute(
            "SELECT id, channel_id FROM chat_messages "
            "WHERE expires_at IS NOT NULL AND expires_at < ? AND deleted_at IS NULL",
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return []
        ids = [(row[0], row[1]) for row in rows]
        # Chunk the id list to avoid exceeding SQLite's bound-variable limit
        for offset in range(0, len(ids), _CHUNK):
            chunk = ids[offset : offset + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            await self._db.execute(
                f"UPDATE chat_messages SET deleted_at = ? "
                f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                (now, *[r[0] for r in chunk]),
            )
        await self._db.commit()
        return ids

    async def get_channel_threads(self, channel_id: str) -> list[dict]:
        """Return parents of all threads in a channel, with reply counts."""
        async with self._db.execute(
            """SELECT
                 parent.*,
                 COUNT(reply.id) AS reply_count,
                 MAX(reply.created_at) AS last_reply_at
               FROM chat_messages parent
               INNER JOIN chat_messages reply
                 ON reply.thread_id = parent.id
                 AND reply.channel_id = parent.channel_id
                 AND reply.deleted_at IS NULL
               WHERE parent.channel_id = ?
                 AND parent.deleted_at IS NULL
               GROUP BY parent.id
               ORDER BY last_reply_at DESC""",
            (channel_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            description = cursor.description
        return [_parse(row, description) for row in rows]

    async def delete_channel_messages(self, channel_id: str) -> int:
        """Delete all messages for a channel. Returns the number of rows deleted."""
        cursor = await self._db.execute(
            "DELETE FROM chat_messages WHERE channel_id = ?", (channel_id,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def update_state(self, message_id: str, state: str) -> None:
        await self._db.execute(
            "UPDATE chat_messages SET state = ? WHERE id = ?", (state, message_id)
        )
        await self._db.commit()

    async def pin_message(self, channel_id: str, message_id: str, pinned_by: str) -> None:
        """Pin a message in a channel. Idempotent. Raises ValueError if pin cap
        (50) would be exceeded by a new pin."""
        # Check if already pinned (idempotent)
        async with self._db.execute(
            "SELECT 1 FROM chat_pins WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        ) as cursor:
            already = await cursor.fetchone()
        if already:
            return
        # Check cap
        async with self._db.execute(
            "SELECT COUNT(*) FROM chat_pins WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
        count = row[0] if row else 0
        if count >= PIN_CAP_PER_CHANNEL:
            raise ValueError(f"pin cap ({PIN_CAP_PER_CHANNEL}) reached")
        now = time.time()
        await self._db.execute(
            "INSERT INTO chat_pins (channel_id, message_id, pinned_by, pinned_at) VALUES (?, ?, ?, ?)",
            (channel_id, message_id, pinned_by, now),
        )
        await self._db.commit()

    async def unpin_message(self, channel_id: str, message_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM chat_pins WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def is_pinned(self, message_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM chat_pins WHERE message_id = ?", (message_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get_pins(self, channel_id: str) -> list[dict]:
        """Return pinned messages in this channel, newest pin first, with
        `pinned_by` and `pinned_at` fields merged into each message dict.
        Excludes soft-deleted messages."""
        async with self._db.execute(
            """SELECT m.*, p.pinned_by, p.pinned_at
               FROM chat_messages m
               INNER JOIN chat_pins p
                 ON p.message_id = m.id AND p.channel_id = m.channel_id
               WHERE p.channel_id = ? AND m.deleted_at IS NULL
               ORDER BY p.pinned_at DESC""",
            (channel_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            description = cursor.description
        return [_parse(row, description) for row in rows]

    async def search(
        self,
        query: str,
        channel_id: str | None = None,
        author_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        pattern = f"%{query}%"
        conditions = ["content LIKE ?"]
        params: list = [pattern]
        if channel_id is not None:
            conditions.append("channel_id = ?")
            params.append(channel_id)
        if author_id is not None:
            conditions.append("author_id = ?")
            params.append(author_id)
        params.append(limit)
        sql = f"SELECT * FROM chat_messages WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT ?"
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            desc = cursor.description
        return [_parse(r, desc) for r in rows]
