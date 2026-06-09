from __future__ import annotations

import json
import time
import uuid
import logging
from pathlib import Path

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

KNOWLEDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    media_path TEXT,
    thumbnail TEXT,
    categories TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    monitor TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    user_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ki_source_type ON knowledge_items(source_type);
CREATE INDEX IF NOT EXISTS idx_ki_status ON knowledge_items(status);
CREATE INDEX IF NOT EXISTS idx_ki_created ON knowledge_items(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ki_user_id ON knowledge_items(user_id);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    id UNINDEXED,
    title,
    content,
    summary,
    author,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS knowledge_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    snapshot_at REAL NOT NULL,
    content_hash TEXT NOT NULL,
    diff_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ks_item ON knowledge_snapshots(item_id, snapshot_at DESC);

CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    match_on TEXT NOT NULL,
    category TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_knowledge_subscriptions (
    agent_name TEXT NOT NULL,
    category TEXT NOT NULL,
    auto_ingest INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_name, category)
);
"""


def _row_to_item(row: tuple) -> dict:
    return {
        "id": row[0],
        "source_type": row[1],
        "source_url": row[2],
        "source_id": row[3],
        "title": row[4],
        "author": row[5],
        "summary": row[6],
        "content": row[7],
        "media_path": row[8],
        "thumbnail": row[9],
        "categories": json.loads(row[10] or "[]"),
        "tags": json.loads(row[11] or "[]"),
        "metadata": json.loads(row[12] or "{}"),
        "status": row[13],
        "monitor": json.loads(row[14] or "{}"),
        "created_at": row[15],
        "updated_at": row[16],
        "user_id": row[17] if len(row) > 17 else "",
    }


class KnowledgeStore(BaseStore):
    """SQLite + FTS5 store for the Knowledge Base Service."""

    SCHEMA = KNOWLEDGE_SCHEMA
    MIGRATIONS = [
        (1, "ALTER TABLE knowledge_items ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"),
    ]

    def __init__(self, db_path: Path, media_dir: Path | None = None) -> None:
        super().__init__(db_path)
        self.media_dir = media_dir or db_path.parent / "knowledge-media"

    async def _post_init(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    async def add_item(
        self,
        source_type: str,
        source_url: str,
        title: str,
        author: str,
        content: str,
        summary: str,
        categories: list[str],
        tags: list[str],
        metadata: dict,
        source_id: str | None = None,
        media_path: str | None = None,
        thumbnail: str | None = None,
        status: str = "pending",
        monitor: dict | None = None,
        user_id: str = "",
    ) -> str:
        """Insert a new KnowledgeItem and add it to the FTS index. Returns the item id."""
        assert self._db is not None
        item_id = str(uuid.uuid4())
        now = time.time()
        await self._db.execute(
            """INSERT INTO knowledge_items
               (id, source_type, source_url, source_id, title, author, summary,
                content, media_path, thumbnail, categories, tags, metadata,
                status, monitor, created_at, updated_at, user_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id, source_type, source_url, source_id, title, author,
                summary, content, media_path, thumbnail,
                json.dumps(categories), json.dumps(tags), json.dumps(metadata),
                status, json.dumps(monitor or {}), now, now, user_id,
            ),
        )
        await self._db.execute(
            "INSERT INTO knowledge_fts (id, title, content, summary, author) VALUES (?,?,?,?,?)",
            (item_id, title, content, summary, author),
        )
        await self._db.commit()
        return item_id

    async def get_item(self, item_id: str, user_id: str | None = None) -> dict | None:
        """Fetch a single item by id.

        When *user_id* is given, only items owned by that user are returned
        (existence-hiding: returns None for items owned by other users).
        Pass ``user_id=None`` (default) to skip ownership filtering — used
        internally and by admin callers.
        """
        assert self._db is not None
        if user_id is not None:
            cursor = await self._db.execute(
                """SELECT id, source_type, source_url, source_id, title, author, summary,
                          content, media_path, thumbnail, categories, tags, metadata,
                          status, monitor, created_at, updated_at, user_id
                   FROM knowledge_items WHERE id = ? AND user_id = ?""",
                (item_id, user_id),
            )
        else:
            cursor = await self._db.execute(
                """SELECT id, source_type, source_url, source_id, title, author, summary,
                          content, media_path, thumbnail, categories, tags, metadata,
                          status, monitor, created_at, updated_at, user_id
                   FROM knowledge_items WHERE id = ?""",
                (item_id,),
            )
        row = await cursor.fetchone()
        return _row_to_item(row) if row else None

    async def update_status(self, item_id: str, status: str) -> None:
        """Update the processing status of an item."""
        assert self._db is not None
        await self._db.execute(
            "UPDATE knowledge_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), item_id),
        )
        await self._db.commit()

    async def update_item(self, item_id: str, **fields) -> None:
        """Update arbitrary fields on an item. JSON-serialises list/dict values."""
        assert self._db is not None
        json_fields = {"categories", "tags", "metadata", "monitor"}
        set_clauses = []
        params = []
        for k, v in fields.items():
            set_clauses.append(f"{k} = ?")
            params.append(json.dumps(v) if k in json_fields else v)
        set_clauses.append("updated_at = ?")
        params.append(time.time())
        params.append(item_id)
        await self._db.execute(
            f"UPDATE knowledge_items SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        # Sync FTS for content-bearing fields
        fts_fields = {"title", "content", "summary", "author"}
        if fts_fields & set(fields.keys()):
            item = await self.get_item(item_id)
            if item:
                await self._db.execute(
                    "INSERT OR REPLACE INTO knowledge_fts (id, title, content, summary, author) VALUES (?,?,?,?,?)",
                    (item_id, item["title"], item["content"], item["summary"], item["author"]),
                )
        await self._db.commit()

    async def list_items(
        self,
        source_type: str | None = None,
        status: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
        user_id: str | None = None,
    ) -> list[dict]:
        """List items with optional filters, newest first.

        When *user_id* is given, only items owned by that user are returned.
        Pass ``user_id=None`` (default) to list all items (admin / internal use).
        """
        assert self._db is not None
        sql = """SELECT id, source_type, source_url, source_id, title, author, summary,
                        content, media_path, thumbnail, categories, tags, metadata,
                        status, monitor, created_at, updated_at, user_id
                 FROM knowledge_items WHERE 1=1"""
        params: list = []
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            # Use json_each() instead of a LIKE scan so this is an exact
            # match on a JSON array element rather than a substring scan.
            sql += (
                " AND id IN ("
                "SELECT ki2.id FROM knowledge_items ki2, "
                "json_each(ki2.categories) WHERE json_each.value = ?"
                ")"
            )
            params.append(category)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_item(r) for r in rows]

    async def list_for_user(self, user_id: str, **kwargs) -> list[dict]:
        """List items belonging to a specific user. Convenience wrapper around list_items."""
        return await self.list_items(user_id=user_id, **kwargs)

    async def delete_item(self, item_id: str) -> bool:
        """Delete an item and its FTS entry. Returns True if a row was deleted."""
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM knowledge_items WHERE id = ?", (item_id,)
        )
        await self._db.execute("DELETE FROM knowledge_fts WHERE id = ?", (item_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # FTS search
    # ------------------------------------------------------------------

    async def search_fts(self, query: str, limit: int = 20, user_id: str | None = None) -> list[dict]:
        """Keyword search across title, content, summary, author using FTS5.

        When *user_id* is given, results are restricted to items owned by that user.
        Pass ``user_id=None`` (default) to search across all items (admin / internal).
        """
        assert self._db is not None
        # Wrap as a quoted phrase so FTS5 operators in user input are not
        # interpreted (AND, OR, NOT, *, NEAR, column:filter, etc.).
        safe_query = '"' + query.replace('"', '""') + '"'
        if user_id is not None:
            sql = """
                SELECT i.id, i.source_type, i.source_url, i.source_id, i.title, i.author,
                       i.summary, i.content, i.media_path, i.thumbnail, i.categories,
                       i.tags, i.metadata, i.status, i.monitor, i.created_at, i.updated_at,
                       i.user_id
                FROM knowledge_fts f
                JOIN knowledge_items i ON i.id = f.id
                WHERE knowledge_fts MATCH ? AND i.user_id = ?
                ORDER BY rank
                LIMIT ?
            """
            fts_params = (safe_query, user_id, limit)
            fallback_params_fn = lambda pattern: (pattern, pattern, pattern, user_id, limit)
            fallback_sql = """
                SELECT id, source_type, source_url, source_id, title, author, summary,
                       content, media_path, thumbnail, categories, tags, metadata,
                       status, monitor, created_at, updated_at, user_id
                FROM knowledge_items
                WHERE (title LIKE ? OR content LIKE ? OR summary LIKE ?) AND user_id = ?
                ORDER BY created_at DESC LIMIT ?
            """
        else:
            sql = """
                SELECT i.id, i.source_type, i.source_url, i.source_id, i.title, i.author,
                       i.summary, i.content, i.media_path, i.thumbnail, i.categories,
                       i.tags, i.metadata, i.status, i.monitor, i.created_at, i.updated_at,
                       i.user_id
                FROM knowledge_fts f
                JOIN knowledge_items i ON i.id = f.id
                WHERE knowledge_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            fts_params = (safe_query, limit)
            fallback_params_fn = lambda pattern: (pattern, pattern, pattern, limit)
            fallback_sql = """
                SELECT id, source_type, source_url, source_id, title, author, summary,
                       content, media_path, thumbnail, categories, tags, metadata,
                       status, monitor, created_at, updated_at, user_id
                FROM knowledge_items
                WHERE title LIKE ? OR content LIKE ? OR summary LIKE ?
                ORDER BY created_at DESC LIMIT ?
            """
        try:
            cursor = await self._db.execute(sql, fts_params)
            rows = await cursor.fetchall()
        except Exception:
            # Fallback to LIKE when FTS query syntax is invalid
            pattern = f"%{query}%"
            cursor = await self._db.execute(fallback_sql, fallback_params_fn(pattern))
            rows = await cursor.fetchall()
        return [_row_to_item(r) for r in rows]

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    async def add_snapshot(
        self,
        item_id: str,
        content_hash: str,
        diff_json: dict | None = None,
        metadata_json: dict | None = None,
    ) -> int:
        """Record a monitoring snapshot for an item. Returns snapshot id."""
        assert self._db is not None
        cursor = await self._db.execute(
            """INSERT INTO knowledge_snapshots
               (item_id, snapshot_at, content_hash, diff_json, metadata_json)
               VALUES (?,?,?,?,?)""",
            (
                item_id, time.time(), content_hash,
                json.dumps(diff_json or {}), json.dumps(metadata_json or {}),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def list_snapshots(self, item_id: str, limit: int = 20) -> list[dict]:
        """List snapshots for an item, newest first."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT id, item_id, snapshot_at, content_hash, diff_json, metadata_json
               FROM knowledge_snapshots WHERE item_id = ?
               ORDER BY snapshot_at DESC LIMIT ?""",
            (item_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "item_id": r[1], "snapshot_at": r[2],
                "content_hash": r[3],
                "diff_json": json.loads(r[4] or "{}"),
                "metadata_json": json.loads(r[5] or "{}"),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Category rules
    # ------------------------------------------------------------------

    async def add_rule(
        self, pattern: str, match_on: str, category: str, priority: int = 0
    ) -> int:
        """Insert a category rule. Returns the new rule id."""
        assert self._db is not None
        cursor = await self._db.execute(
            "INSERT INTO category_rules (pattern, match_on, category, priority) VALUES (?,?,?,?)",
            (pattern, match_on, category, priority),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def list_rules(self) -> list[dict]:
        """List all category rules ordered by priority descending."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT id, pattern, match_on, category, priority FROM category_rules ORDER BY priority DESC"
        )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "pattern": r[1], "match_on": r[2], "category": r[3], "priority": r[4]}
            for r in rows
        ]

    async def delete_rule(self, rule_id: int) -> bool:
        """Delete a category rule by id."""
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM category_rules WHERE id = ?", (rule_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Agent subscriptions
    # ------------------------------------------------------------------

    async def set_subscription(
        self, agent_name: str, category: str, auto_ingest: bool
    ) -> None:
        """Upsert an agent subscription for a category."""
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO agent_knowledge_subscriptions
               (agent_name, category, auto_ingest) VALUES (?,?,?)""",
            (agent_name, category, int(auto_ingest)),
        )
        await self._db.commit()

    async def delete_subscription(self, agent_name: str, category: str) -> bool:
        """Remove an agent subscription."""
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM agent_knowledge_subscriptions WHERE agent_name = ? AND category = ?",
            (agent_name, category),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_subscriptions(self, agent_name: str | None = None) -> list[dict]:
        """List subscriptions, optionally filtered by agent."""
        assert self._db is not None
        sql = "SELECT agent_name, category, auto_ingest FROM agent_knowledge_subscriptions"
        params: list = []
        if agent_name:
            sql += " WHERE agent_name = ?"
            params.append(agent_name)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {"agent_name": r[0], "category": r[1], "auto_ingest": bool(r[2])}
            for r in rows
        ]

    async def subscribers_for_categories(self, categories: list[str]) -> list[dict]:
        """Return subscriptions whose category matches any of the given categories."""
        assert self._db is not None
        if not categories:
            return []
        placeholders = ",".join("?" * len(categories))
        cursor = await self._db.execute(
            f"SELECT agent_name, category, auto_ingest FROM agent_knowledge_subscriptions WHERE category IN ({placeholders})",
            categories,
        )
        rows = await cursor.fetchall()
        return [
            {"agent_name": r[0], "category": r[1], "auto_ingest": bool(r[2])}
            for r in rows
        ]
