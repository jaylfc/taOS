from __future__ import annotations

import hashlib
import json
import time

from tinyagentos.base_store import BaseStore

DEFAULT_SETTINGS = {
    "capture_conversations": True,
    "capture_files": True,
    "capture_searches": False,
    "capture_notes": True,
}


# Partition every chunk was written under before captures were per user.
LEGACY_USER_ID = "user"


class UserMemoryStore(BaseStore):
    """Per-user memory chunks.

    Each person's chunks live under their own user id.  The install owner
    (resolved through ``set_owner_lookup``) additionally reads the legacy
    ``"user"`` partition written before captures were per user, so nothing they
    had disappears.  The legacy rows are never copied or rewritten, and no
    other user ever sees them.
    """

    _owner_lookup = None

    def set_owner_lookup(self, fn) -> None:
        """``fn()`` returns the install owner's user id, or None."""
        self._owner_lookup = fn

    def can_read_legacy(self, user_id: str) -> bool:
        """True when *user_id* is the install owner (reads the legacy partition)."""
        return LEGACY_USER_ID in self._scope(user_id) and user_id != LEGACY_USER_ID

    def _scope(self, user_id: str) -> tuple[str, ...]:
        """User ids whose rows *user_id* may read."""
        if user_id == LEGACY_USER_ID or self._owner_lookup is None:
            return (user_id,)
        owner = self._owner_lookup()
        if owner and owner == user_id:
            return (user_id, LEGACY_USER_ID)
        return (user_id,)

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS user_memory_chunks (
        hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        collection TEXT NOT NULL,
        title TEXT DEFAULT '',
        content TEXT NOT NULL,
        metadata TEXT DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_um_user ON user_memory_chunks(user_id, collection);
    CREATE INDEX IF NOT EXISTS idx_um_created ON user_memory_chunks(created_at);

    CREATE VIRTUAL TABLE IF NOT EXISTS user_memory_fts USING fts5(
        hash UNINDEXED,
        title,
        content,
        tokenize='porter unicode61'
    );

    CREATE TABLE IF NOT EXISTS user_memory_settings (
        user_id TEXT PRIMARY KEY,
        settings TEXT NOT NULL DEFAULT '{}'
    );
    """

    async def save_chunk(
        self,
        user_id: str,
        content: str,
        title: str = "",
        collection: str = "snippets",
        metadata: dict | None = None,
    ) -> str:
        assert self._db is not None
        h = hashlib.sha256(
            f"{user_id}|{collection}|{content}".encode()
        ).hexdigest()[:16]
        await self._db.execute(
            "INSERT OR REPLACE INTO user_memory_chunks (hash, user_id, collection, title, content, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (h, user_id, collection, title, content, json.dumps(metadata or {}), time.time()),
        )
        await self._db.execute(
            "INSERT OR REPLACE INTO user_memory_fts (hash, title, content) VALUES (?, ?, ?)",
            (h, title, content),
        )
        await self._db.commit()
        return h

    async def search(
        self,
        user_id: str,
        query: str,
        collection: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        assert self._db is not None
        scope = self._scope(user_id)
        ph = ",".join("?" * len(scope))
        # Wrap as a quoted phrase: double internal quotes first, then wrap the
        # whole string in double-quotes.  This prevents FTS5 special characters
        # (AND, OR, NOT, ^, *, NEAR, column filters, etc.) from being
        # interpreted as query operators.
        fts_query = '"' + query.replace('"', '""') + '"'
        sql = """
            SELECT c.hash, c.collection, c.title, c.content, c.metadata, c.created_at
            FROM user_memory_fts f
            JOIN user_memory_chunks c ON c.hash = f.hash
            WHERE f.content MATCH ? AND c.user_id IN ({ph})
        """.format(ph=ph)
        params: list = [fts_query, *scope]
        if collection:
            sql += " AND c.collection = ?"
            params.append(collection)
        sql += " ORDER BY c.created_at DESC LIMIT ?"
        params.append(limit)

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except Exception:
            like_sql = """
                SELECT hash, collection, title, content, metadata, created_at
                FROM user_memory_chunks
                WHERE user_id IN ({ph}) AND (title LIKE ? OR content LIKE ?)
            """.format(ph=ph)
            like_params: list = [*scope, f"%{query}%", f"%{query}%"]
            if collection:
                like_sql += " AND collection = ?"
                like_params.append(collection)
            like_sql += " ORDER BY created_at DESC LIMIT ?"
            like_params.append(limit)
            cursor = await self._db.execute(like_sql, like_params)
            rows = await cursor.fetchall()

        return [
            {
                "hash": r[0],
                "collection": r[1],
                "title": r[2],
                "content": r[3],
                "metadata": json.loads(r[4] or "{}"),
                "created_at": r[5],
            }
            for r in rows
        ]

    async def browse(
        self,
        user_id: str,
        collection: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        assert self._db is not None
        scope = self._scope(user_id)
        sql = (
            "SELECT hash, collection, title, content, metadata, created_at "
            "FROM user_memory_chunks WHERE user_id IN (%s)" % ",".join("?" * len(scope))
        )
        params: list = [*scope]
        if collection:
            sql += " AND collection = ?"
            params.append(collection)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "hash": r[0],
                "collection": r[1],
                "title": r[2],
                "content": r[3],
                "metadata": json.loads(r[4] or "{}"),
                "created_at": r[5],
            }
            for r in rows
        ]

    async def delete_chunk(self, user_id: str, chunk_hash: str) -> bool:
        assert self._db is not None
        scope = self._scope(user_id)
        cursor = await self._db.execute(
            "DELETE FROM user_memory_chunks WHERE hash = ? AND user_id IN (%s)"
            % ",".join("?" * len(scope)),
            (chunk_hash, *scope),
        )
        await self._db.execute(
            "DELETE FROM user_memory_fts WHERE hash = ?", (chunk_hash,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_stats(self, user_id: str) -> dict:
        assert self._db is not None
        scope = self._scope(user_id)
        cursor = await self._db.execute(
            "SELECT collection, COUNT(*) FROM user_memory_chunks WHERE user_id IN (%s) "
            "GROUP BY collection" % ",".join("?" * len(scope)),
            scope,
        )
        rows = await cursor.fetchall()
        collections = {r[0]: r[1] for r in rows}
        total = sum(collections.values())
        return {"total": total, "collections": collections}

    async def get_settings(self, user_id: str) -> dict:
        assert self._db is not None
        row = None
        # Own settings first; the owner inherits the legacy row until they
        # save their own.
        for uid in self._scope(user_id):
            cursor = await self._db.execute(
                "SELECT settings FROM user_memory_settings WHERE user_id = ?",
                (uid,),
            )
            row = await cursor.fetchone()
            if row:
                break
        if row:
            return {**DEFAULT_SETTINGS, **json.loads(row[0])}
        return dict(DEFAULT_SETTINGS)

    async def update_settings(self, user_id: str, updates: dict) -> None:
        assert self._db is not None
        current = await self.get_settings(user_id)
        current.update(updates)
        await self._db.execute(
            "INSERT OR REPLACE INTO user_memory_settings (user_id, settings) VALUES (?, ?)",
            (user_id, json.dumps(current)),
        )
        await self._db.commit()
