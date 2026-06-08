from __future__ import annotations
import json
import time
import hashlib

from tinyagentos.base_store import BaseStore

DEFAULT_SETTINGS = {
    "capture_conversations": True,
    "capture_files": True,
    "capture_searches": False,
    "capture_notes": True,
}


class UserMemoryStore(BaseStore):
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
        # Wrap as a quoted phrase: double internal quotes first, then wrap the
        # whole string in double-quotes.  This prevents FTS5 special characters
        # (AND, OR, NOT, ^, *, NEAR, column filters, etc.) from being
        # interpreted as query operators.
        fts_query = '"' + query.replace('"', '""') + '"'
        sql = """
            SELECT c.hash, c.collection, c.title, c.content, c.metadata, c.created_at
            FROM user_memory_fts f
            JOIN user_memory_chunks c ON c.hash = f.hash
            WHERE f.content MATCH ? AND c.user_id = ?
        """
        params: list = [fts_query, user_id]
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
                WHERE user_id = ? AND (title LIKE ? OR content LIKE ?)
            """
            like_params: list = [user_id, f"%{query}%", f"%{query}%"]
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
    ) -> list[dict]:
        assert self._db is not None
        sql = "SELECT hash, collection, title, content, metadata, created_at FROM user_memory_chunks WHERE user_id = ?"
        params: list = [user_id]
        if collection:
            sql += " AND collection = ?"
            params.append(collection)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
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
        cursor = await self._db.execute(
            "DELETE FROM user_memory_chunks WHERE hash = ? AND user_id = ?",
            (chunk_hash, user_id),
        )
        await self._db.execute(
            "DELETE FROM user_memory_fts WHERE hash = ?", (chunk_hash,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_stats(self, user_id: str) -> dict:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT collection, COUNT(*) FROM user_memory_chunks WHERE user_id = ? GROUP BY collection",
            (user_id,),
        )
        rows = await cursor.fetchall()
        collections = {r[0]: r[1] for r in rows}
        total = sum(collections.values())
        return {"total": total, "collections": collections}

    async def get_settings(self, user_id: str) -> dict:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT settings FROM user_memory_settings WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
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
