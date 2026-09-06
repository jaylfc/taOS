# Knowledge Base Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Knowledge Base Service backend: a SQLite + FTS5 store, async ingest pipeline, monitor service with smart decay, category engine, access control, and API routes under `/api/knowledge/`.

**Architecture:** `KnowledgeStore` (BaseStore subclass) owns all SQLite tables and FTS5 index. `IngestPipeline` runs as an async background task — it resolves the URL type, downloads content, calls an LLM for summary and categories, embeds via QMD, writes to the store, and fires notifications. `MonitorService` runs a 60-second poll loop, re-fetches monitored items, diffs snapshots, and applies smart decay to polling intervals. `CategoryEngine` checks rule-based matches first and falls back to LLM only for unmatched items. All five components are wired into `create_app` in `app.py` and exposed under the `/api/knowledge/` prefix.

**Tech Stack:** Python 3.10+, FastAPI APIRouter, aiosqlite, httpx, SQLite FTS5, QMD vector search (`/ingest` + `/vsearch` endpoints), existing `NotificationStore` for event emission, `BaseStore` pattern from `tinyagentos/base_store.py`.

---

## File Map

| File | Role |
|---|---|
| `tinyagentos/knowledge_store.py` | KnowledgeStore — all schema, CRUD, FTS5 search, snapshots, rules, subscriptions |
| `tinyagentos/knowledge_ingest.py` | IngestPipeline — URL resolution, download (article only in Step 1), summarise, embed, store, notify |
| `tinyagentos/knowledge_monitor.py` | MonitorService — 60s poll loop, snapshot diff, smart decay |
| `tinyagentos/knowledge_categories.py` | CategoryEngine — rule matching + LLM fallback |
| `tinyagentos/routes/knowledge.py` | FastAPI router — all `/api/knowledge/` endpoints |
| `tinyagentos/app.py` | Wire stores and router into create_app |
| `tests/test_knowledge_store.py` | Unit tests for KnowledgeStore |
| `tests/test_knowledge_ingest.py` | Unit tests for IngestPipeline |
| `tests/test_knowledge_monitor.py` | Unit tests for MonitorService |
| `tests/test_knowledge_categories.py` | Unit tests for CategoryEngine |
| `tests/test_knowledge_routes.py` | Integration tests for API routes |

---

### Task 1: KnowledgeStore — schema and basic CRUD

**Files:**
- Create: `tinyagentos/knowledge_store.py`
- Create: `tests/test_knowledge_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_knowledge_store.py
from __future__ import annotations
import json
import time
import pytest
import pytest_asyncio
from pathlib import Path
from tinyagentos.knowledge_store import KnowledgeStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "knowledge-media")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_add_and_get_item(store):
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/post",
        title="Test Article",
        author="tester",
        content="Full text of the article goes here.",
        summary="A brief summary.",
        categories=["Tech"],
        tags=["python"],
        metadata={"word_count": 8},
    )
    assert item_id  # non-empty string
    item = await store.get_item(item_id)
    assert item is not None
    assert item["title"] == "Test Article"
    assert item["source_type"] == "article"
    assert item["status"] == "pending"
    assert item["categories"] == ["Tech"]
    assert item["tags"] == ["python"]


@pytest.mark.asyncio
async def test_get_item_not_found(store):
    item = await store.get_item("nonexistent-id")
    assert item is None


@pytest.mark.asyncio
async def test_update_status(store):
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/post2",
        title="Another Article",
        author="tester",
        content="Content.",
        summary="Summary.",
        categories=[],
        tags=[],
        metadata={},
    )
    await store.update_status(item_id, "ready")
    item = await store.get_item(item_id)
    assert item["status"] == "ready"


@pytest.mark.asyncio
async def test_list_items(store):
    for i in range(3):
        await store.add_item(
            source_type="article",
            source_url=f"https://example.com/{i}",
            title=f"Article {i}",
            author="tester",
            content="Content.",
            summary="Summary.",
            categories=["Tech"],
            tags=[],
            metadata={},
        )
    items = await store.list_items(limit=10)
    assert len(items) == 3


@pytest.mark.asyncio
async def test_delete_item(store):
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/del",
        title="To Delete",
        author="tester",
        content="Content.",
        summary="Summary.",
        categories=[],
        tags=[],
        metadata={},
    )
    deleted = await store.delete_item(item_id)
    assert deleted is True
    assert await store.get_item(item_id) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_store.py -v 2>&1 | head -30
```

Expected: `ERROR` or `ModuleNotFoundError: No module named 'tinyagentos.knowledge_store'`

- [ ] **Step 3: Implement KnowledgeStore with schema and CRUD**

```python
# tinyagentos/knowledge_store.py
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
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ki_source_type ON knowledge_items(source_type);
CREATE INDEX IF NOT EXISTS idx_ki_status ON knowledge_items(status);
CREATE INDEX IF NOT EXISTS idx_ki_created ON knowledge_items(created_at DESC);

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
    }


class KnowledgeStore(BaseStore):
    """SQLite + FTS5 store for the Knowledge Base Service."""

    SCHEMA = KNOWLEDGE_SCHEMA

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
    ) -> str:
        """Insert a new KnowledgeItem and add it to the FTS index. Returns the item id."""
        assert self._db is not None
        item_id = str(uuid.uuid4())
        now = time.time()
        await self._db.execute(
            """INSERT INTO knowledge_items
               (id, source_type, source_url, source_id, title, author, summary,
                content, media_path, thumbnail, categories, tags, metadata,
                status, monitor, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id, source_type, source_url, source_id, title, author,
                summary, content, media_path, thumbnail,
                json.dumps(categories), json.dumps(tags), json.dumps(metadata),
                status, json.dumps(monitor or {}), now, now,
            ),
        )
        await self._db.execute(
            "INSERT INTO knowledge_fts (id, title, content, summary, author) VALUES (?,?,?,?,?)",
            (item_id, title, content, summary, author),
        )
        await self._db.commit()
        return item_id

    async def get_item(self, item_id: str) -> dict | None:
        """Fetch a single item by id. Returns None if not found."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT id, source_type, source_url, source_id, title, author, summary,
                      content, media_path, thumbnail, categories, tags, metadata,
                      status, monitor, created_at, updated_at
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
    ) -> list[dict]:
        """List items with optional filters, newest first."""
        assert self._db is not None
        sql = """SELECT id, source_type, source_url, source_id, title, author, summary,
                        content, media_path, thumbnail, categories, tags, metadata,
                        status, monitor, created_at, updated_at
                 FROM knowledge_items WHERE 1=1"""
        params: list = []
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND categories LIKE ?"
            params.append(f'%"{category}"%')
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_item(r) for r in rows]

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

    async def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        """Keyword search across title, content, summary, author using FTS5."""
        assert self._db is not None
        safe_query = query.replace('"', '""')
        sql = """
            SELECT i.id, i.source_type, i.source_url, i.source_id, i.title, i.author,
                   i.summary, i.content, i.media_path, i.thumbnail, i.categories,
                   i.tags, i.metadata, i.status, i.monitor, i.created_at, i.updated_at
            FROM knowledge_fts f
            JOIN knowledge_items i ON i.id = f.id
            WHERE f.knowledge_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        try:
            cursor = await self._db.execute(sql, (safe_query, limit))
            rows = await cursor.fetchall()
        except Exception:
            # Fallback to LIKE when FTS query syntax is invalid
            fallback = """
                SELECT id, source_type, source_url, source_id, title, author, summary,
                       content, media_path, thumbnail, categories, tags, metadata,
                       status, monitor, created_at, updated_at
                FROM knowledge_items
                WHERE title LIKE ? OR content LIKE ? OR summary LIKE ?
                ORDER BY created_at DESC LIMIT ?
            """
            pattern = f"%{query}%"
            cursor = await self._db.execute(fallback, (pattern, pattern, pattern, limit))
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_store.py -v
```

Expected output:
```
tests/test_knowledge_store.py::test_add_and_get_item PASSED
tests/test_knowledge_store.py::test_get_item_not_found PASSED
tests/test_knowledge_store.py::test_update_status PASSED
tests/test_knowledge_store.py::test_list_items PASSED
tests/test_knowledge_store.py::test_delete_item PASSED
5 passed
```

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos
git add tinyagentos/knowledge_store.py tests/test_knowledge_store.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "feat(knowledge): add KnowledgeStore with SQLite + FTS5 schema and CRUD"
```

---

### Task 2: KnowledgeStore — FTS search, snapshots, rules, and subscriptions

**Files:**
- Modify: `tests/test_knowledge_store.py` (add tests for the remaining methods)

- [ ] **Step 1: Write failing tests for FTS, snapshots, rules, and subscriptions**

Append to `tests/test_knowledge_store.py`:

```python
@pytest.mark.asyncio
async def test_search_fts(store):
    await store.add_item(
        source_type="article",
        source_url="https://example.com/async",
        title="Async Python Guide",
        author="dev",
        content="asyncio event loop coroutine await",
        summary="Guide to async Python.",
        categories=["Tech"],
        tags=[],
        metadata={},
    )
    await store.add_item(
        source_type="article",
        source_url="https://example.com/rust",
        title="Rust Memory Safety",
        author="dev",
        content="ownership borrowing lifetimes",
        summary="Guide to Rust.",
        categories=["Tech"],
        tags=[],
        metadata={},
    )
    results = await store.search_fts("asyncio")
    assert len(results) == 1
    assert results[0]["title"] == "Async Python Guide"


@pytest.mark.asyncio
async def test_snapshot_roundtrip(store):
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/abc",
        title="Thread",
        author="u/tester",
        content="Original text.",
        summary="Summary.",
        categories=[],
        tags=[],
        metadata={},
    )
    snap_id = await store.add_snapshot(
        item_id, "deadbeef",
        diff_json={"new_comments": 2},
        metadata_json={"upvotes": 100},
    )
    assert snap_id > 0
    snaps = await store.list_snapshots(item_id)
    assert len(snaps) == 1
    assert snaps[0]["content_hash"] == "deadbeef"
    assert snaps[0]["diff_json"]["new_comments"] == 2


@pytest.mark.asyncio
async def test_category_rules_crud(store):
    rule_id = await store.add_rule(
        pattern="LocalLLaMA", match_on="subreddit", category="AI/ML", priority=10
    )
    assert rule_id > 0
    rules = await store.list_rules()
    assert len(rules) == 1
    assert rules[0]["category"] == "AI/ML"
    deleted = await store.delete_rule(rule_id)
    assert deleted is True
    assert await store.list_rules() == []


@pytest.mark.asyncio
async def test_agent_subscriptions(store):
    await store.set_subscription("research-agent", "AI/ML", auto_ingest=True)
    await store.set_subscription("research-agent", "Rockchip", auto_ingest=False)
    subs = await store.list_subscriptions("research-agent")
    assert len(subs) == 2
    categories = {s["category"] for s in subs}
    assert categories == {"AI/ML", "Rockchip"}

    matching = await store.subscribers_for_categories(["AI/ML"])
    assert len(matching) == 1
    assert matching[0]["auto_ingest"] is True

    deleted = await store.delete_subscription("research-agent", "Rockchip")
    assert deleted is True
    subs = await store.list_subscriptions("research-agent")
    assert len(subs) == 1


@pytest.mark.asyncio
async def test_list_items_filter_by_category(store):
    await store.add_item(
        source_type="article",
        source_url="https://example.com/ai",
        title="AI Article",
        author="tester",
        content="content",
        summary="summary",
        categories=["AI/ML"],
        tags=[],
        metadata={},
    )
    await store.add_item(
        source_type="article",
        source_url="https://example.com/other",
        title="Other Article",
        author="tester",
        content="content",
        summary="summary",
        categories=["Other"],
        tags=[],
        metadata={},
    )
    results = await store.list_items(category="AI/ML")
    assert len(results) == 1
    assert results[0]["title"] == "AI Article"
```

- [ ] **Step 2: Run tests to verify new ones fail**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_store.py::test_search_fts tests/test_knowledge_store.py::test_snapshot_roundtrip tests/test_knowledge_store.py::test_category_rules_crud tests/test_knowledge_store.py::test_agent_subscriptions tests/test_knowledge_store.py::test_list_items_filter_by_category -v
```

Expected: All 5 fail with `fixture 'store' not found` — the fixture is defined in the same file but not yet appended. If the fixture is present: all 5 should FAIL because the methods are not yet implemented. If they PASS (they are already implemented from Task 1), proceed directly to Step 4.

- [ ] **Step 3: Confirm all tests pass (all methods were written in Task 1)**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_store.py -v
```

Expected: All 10 tests PASS. If any fail, check the FTS SQL in `search_fts` — the table alias must use the table name not the alias: `WHERE knowledge_fts MATCH ?` (not `f.knowledge_fts MATCH ?`).

- [ ] **Step 4: Commit**

```bash
cd /home/jay/tinyagentos
git add tests/test_knowledge_store.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "test(knowledge): add FTS, snapshot, rule, and subscription tests for KnowledgeStore"
```

---

### Task 3: CategoryEngine — rule matching and LLM fallback

**Files:**
- Create: `tinyagentos/knowledge_categories.py`
- Create: `tests/test_knowledge_categories.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_knowledge_categories.py
from __future__ import annotations
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_categories import CategoryEngine


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    await s.add_rule(pattern="LocalLLaMA", match_on="subreddit", category="AI/ML", priority=10)
    await s.add_rule(pattern="github.com/rockchip*", match_on="source_url", category="Rockchip", priority=5)
    await s.add_rule(pattern="github", match_on="source_type", category="Development", priority=1)
    yield s
    await s.close()


@pytest.fixture
def engine(store):
    return CategoryEngine(store)


@pytest.mark.asyncio
async def test_rule_match_subreddit(engine):
    categories = await engine.categorise(
        source_type="reddit",
        source_url="https://reddit.com/r/LocalLLaMA/comments/abc",
        title="Cool post",
        summary="About LLMs.",
        metadata={"subreddit": "LocalLLaMA"},
    )
    assert "AI/ML" in categories


@pytest.mark.asyncio
async def test_rule_match_source_url_glob(engine):
    categories = await engine.categorise(
        source_type="github",
        source_url="https://github.com/rockchip-linux/rknn-toolkit2",
        title="RKNN Toolkit",
        summary="NPU toolkit.",
        metadata={},
    )
    assert "Rockchip" in categories
    assert "Development" in categories  # source_type=github rule also fires


@pytest.mark.asyncio
async def test_rule_match_source_type(engine):
    categories = await engine.categorise(
        source_type="github",
        source_url="https://github.com/some/repo",
        title="Some Repo",
        summary="Generic repo.",
        metadata={},
    )
    assert "Development" in categories


@pytest.mark.asyncio
async def test_no_rule_match_calls_llm_fallback(engine):
    """When no rules match, the LLM fallback should be called."""
    engine._llm_categorise = AsyncMock(return_value=["Hardware"])
    categories = await engine.categorise(
        source_type="article",
        source_url="https://unknownsite.com/post",
        title="Some obscure post",
        summary="About hardware.",
        metadata={},
    )
    engine._llm_categorise.assert_awaited_once()
    assert "Hardware" in categories


@pytest.mark.asyncio
async def test_glob_wildcard_matching(engine):
    """* in pattern should match any substring."""
    categories = await engine.categorise(
        source_type="github",
        source_url="https://github.com/rockchip-extra/rknpu",
        title="RKNPU",
        summary="NPU driver.",
        metadata={},
    )
    assert "Rockchip" in categories


@pytest.mark.asyncio
async def test_llm_fallback_skipped_when_rules_match(engine):
    """_llm_categorise must NOT be called when a rule already matched."""
    engine._llm_categorise = AsyncMock(return_value=["Irrelevant"])
    await engine.categorise(
        source_type="reddit",
        source_url="https://reddit.com/r/LocalLLaMA/comments/abc",
        title="Post",
        summary="Summary.",
        metadata={"subreddit": "LocalLLaMA"},
    )
    engine._llm_categorise.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_categories.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'tinyagentos.knowledge_categories'`

- [ ] **Step 3: Implement CategoryEngine**

```python
# tinyagentos/knowledge_categories.py
from __future__ import annotations

import fnmatch
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyagentos.knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)


class CategoryEngine:
    """Assigns categories to KnowledgeItems.

    Rule-based matching runs first (free). LLM fallback fires only
    when no rules produce a match.
    """

    def __init__(self, store: "KnowledgeStore", http_client=None, llm_url: str = "") -> None:
        self._store = store
        self._http_client = http_client
        self._llm_url = llm_url

    async def categorise(
        self,
        source_type: str,
        source_url: str,
        title: str,
        summary: str,
        metadata: dict,
    ) -> list[str]:
        """Return a list of category strings for the given item attributes.

        Checks all rules in priority order. A rule's pattern uses glob
        matching (``*`` wildcard). Multiple rules can match, each adding
        its category. If no rules match, ``_llm_categorise`` is called.
        """
        rules = await self._store.list_rules()
        matched: list[str] = []

        # Build lookup values for each match_on field
        lookup: dict[str, str] = {
            "source_type": source_type,
            "source_url": source_url,
            "title": title.lower(),
            "subreddit": metadata.get("subreddit", ""),
            "channel": metadata.get("channel", ""),
            "author": metadata.get("author", ""),
        }

        for rule in rules:
            field_value = lookup.get(rule["match_on"], "")
            if fnmatch.fnmatch(field_value, rule["pattern"]) or fnmatch.fnmatch(
                field_value.lower(), rule["pattern"].lower()
            ):
                if rule["category"] not in matched:
                    matched.append(rule["category"])

        if not matched:
            try:
                matched = await self._llm_categorise(
                    source_type=source_type,
                    source_url=source_url,
                    title=title,
                    summary=summary,
                )
            except Exception as exc:
                logger.warning("LLM category fallback failed: %s", exc)

        return matched

    async def _llm_categorise(
        self,
        source_type: str,
        source_url: str,
        title: str,
        summary: str,
    ) -> list[str]:
        """Call the LLM to suggest 1-3 categories for an unmatched item.

        Sends a short prompt to the configured LLM endpoint. Returns an
        empty list if the LLM is unavailable, so the caller can still
        proceed without categories.
        """
        if not self._http_client or not self._llm_url:
            return []

        # Fetch existing categories to guide the LLM
        rules = await self._store.list_rules()
        existing_cats = list({r["category"] for r in rules})

        prompt = (
            f"You are categorising a saved knowledge item.\n"
            f"Title: {title}\n"
            f"Source type: {source_type}\n"
            f"URL: {source_url}\n"
            f"Summary: {summary}\n"
            f"Existing categories: {', '.join(existing_cats) if existing_cats else 'none yet'}\n\n"
            f"Respond with a JSON array of 1-3 category strings. "
            f"Prefer existing categories when they fit. "
            f"Only propose a new category if none fit. "
            f"Example: [\"AI/ML\", \"Development\"]"
        )

        resp = await self._http_client.post(
            self._llm_url,
            json={"prompt": prompt, "max_tokens": 60},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("text", data.get("content", "[]"))

        import json
        try:
            categories = json.loads(raw)
            if isinstance(categories, list):
                return [str(c) for c in categories[:3]]
        except Exception:
            pass
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_categories.py -v
```

Expected:
```
tests/test_knowledge_categories.py::test_rule_match_subreddit PASSED
tests/test_knowledge_categories.py::test_rule_match_source_url_glob PASSED
tests/test_knowledge_categories.py::test_rule_match_source_type PASSED
tests/test_knowledge_categories.py::test_no_rule_match_calls_llm_fallback PASSED
tests/test_knowledge_categories.py::test_glob_wildcard_matching PASSED
tests/test_knowledge_categories.py::test_llm_fallback_skipped_when_rules_match PASSED
6 passed
```

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos
git add tinyagentos/knowledge_categories.py tests/test_knowledge_categories.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "feat(knowledge): add CategoryEngine with glob rule matching and LLM fallback"
```

---

### Task 4: IngestPipeline — URL resolution and article download

**Files:**
- Create: `tinyagentos/knowledge_ingest.py`
- Create: `tests/test_knowledge_ingest.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_knowledge_ingest.py
from __future__ import annotations
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_ingest import IngestPipeline, resolve_source_type


# --- URL resolution ---

def test_resolve_reddit():
    assert resolve_source_type("https://reddit.com/r/LocalLLaMA/comments/abc") == "reddit"
    assert resolve_source_type("https://www.reddit.com/r/Python/comments/xyz/") == "reddit"


def test_resolve_youtube():
    assert resolve_source_type("https://www.youtube.com/watch?v=abc123") == "youtube"
    assert resolve_source_type("https://youtu.be/abc123") == "youtube"


def test_resolve_x():
    assert resolve_source_type("https://x.com/user/status/123") == "x"
    assert resolve_source_type("https://twitter.com/user/status/456") == "x"


def test_resolve_github():
    assert resolve_source_type("https://github.com/some/repo") == "github"
    assert resolve_source_type("https://github.com/org/repo/issues/1") == "github"


def test_resolve_article_fallback():
    assert resolve_source_type("https://news.ycombinator.com/item?id=123") == "article"
    assert resolve_source_type("https://blog.example.com/some-post") == "article"


# --- IngestPipeline ---

@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def mock_http():
    client = AsyncMock()
    # Default: return minimal HTML for article fetch
    response = MagicMock()
    response.status_code = 200
    response.text = "<html><body><article><p>This is the main article content with enough text to pass the quality threshold.</p></article></body></html>"
    response.raise_for_status = MagicMock()
    client.get = AsyncMock(return_value=response)
    return client


@pytest_asyncio.fixture
async def pipeline(store, mock_http):
    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=["Tech"])
    p = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="",  # QMD disabled for unit tests
        llm_base_url="",  # LLM disabled for unit tests
    )
    return p


@pytest.mark.asyncio
async def test_ingest_creates_pending_item(pipeline, store):
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="",
        text="",
        categories=[],
        source="test",
    )
    assert item_id
    item = await store.get_item(item_id)
    assert item is not None
    assert item["status"] in ("pending", "processing", "ready", "error")
    assert item["source_url"] == "https://example.com/article"


@pytest.mark.asyncio
async def test_ingest_article_sets_ready(pipeline, store):
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="",
        text="",
        categories=[],
        source="test",
    )
    # Run the pipeline inline (not background) for testing
    await pipeline.run(item_id)
    item = await store.get_item(item_id)
    assert item["status"] == "ready"
    assert item["source_type"] == "article"


@pytest.mark.asyncio
async def test_ingest_with_text_override(pipeline, store):
    """When text is provided directly, skip HTTP download."""
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="My Title",
        text="Pre-provided content that is long enough to pass quality checks.",
        categories=["Tech"],
        source="share-sheet",
    )
    await pipeline.run(item_id)
    item = await store.get_item(item_id)
    assert item["status"] == "ready"
    assert "Pre-provided content" in item["content"]


@pytest.mark.asyncio
async def test_ingest_notifies_on_ready(pipeline, store):
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="",
        text="",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)
    pipeline._notifications.emit_event.assert_awaited()


@pytest.mark.asyncio
async def test_ingest_sets_error_on_failure(pipeline, store):
    pipeline._http_client.get = AsyncMock(side_effect=Exception("network failure"))
    item_id = await pipeline.submit(
        url="https://example.com/failing",
        title="",
        text="",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)
    item = await store.get_item(item_id)
    assert item["status"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_ingest.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'tinyagentos.knowledge_ingest'`

- [ ] **Step 3: Implement IngestPipeline**

```python
# tinyagentos/knowledge_ingest.py
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx
    from tinyagentos.knowledge_store import KnowledgeStore
    from tinyagentos.knowledge_categories import CategoryEngine
    from tinyagentos.notifications import NotificationStore

logger = logging.getLogger(__name__)

# Quality threshold: minimum chars for readability extraction to count as success
_MIN_CONTENT_CHARS = 100

# Per-source default monitor config
_DEFAULT_MONITOR: dict[str, dict] = {
    "reddit":  {"frequency": 3600,  "decay_rate": 1.5, "stop_after_days": 30,  "pinned": False, "last_poll": 0, "current_interval": 3600},
    "x":       {"frequency": 1800,  "decay_rate": 2.0, "stop_after_days": 14,  "pinned": False, "last_poll": 0, "current_interval": 1800},
    "github":  {"frequency": 21600, "decay_rate": 1.5, "stop_after_days": 60,  "pinned": False, "last_poll": 0, "current_interval": 21600},
    "youtube": {"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 30,  "pinned": False, "last_poll": 0, "current_interval": 86400},
    "article": {"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,  "pinned": False, "last_poll": 0, "current_interval": 86400},
    "file":    {"frequency": 0,     "decay_rate": 1.0, "stop_after_days": 0,   "pinned": False, "last_poll": 0, "current_interval": 0},
    "manual":  {"frequency": 0,     "decay_rate": 1.0, "stop_after_days": 0,   "pinned": False, "last_poll": 0, "current_interval": 0},
}


def resolve_source_type(url: str) -> str:
    """Identify the content platform from a URL.

    Returns one of: reddit, youtube, x, github, article.
    """
    url_lower = url.lower()
    if re.search(r"(^|\.)(reddit\.com)/", url_lower):
        return "reddit"
    if re.search(r"(^|\.)youtube\.com/watch|youtu\.be/", url_lower):
        return "youtube"
    if re.search(r"(^|\.)(x\.com|twitter\.com)/", url_lower):
        return "x"
    if re.search(r"(^|\.)github\.com/", url_lower):
        return "github"
    return "article"


def _extract_text_readability(html: str) -> str:
    """Very lightweight readability extraction: strip tags, collapse whitespace.

    A proper implementation would use a library like ``readability-lxml``.
    This stub is sufficient for unit-tested pipeline flow; swap in a real
    extractor in production without changing the interface.
    """
    # Remove script and style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


class IngestPipeline:
    """Async pipeline that downloads, summarises, embeds, and stores content.

    Call ``submit()`` to create a pending item and return its id immediately.
    Call ``run(item_id)`` to execute the pipeline synchronously (useful in
    tests or when the caller wants to await completion). In production, use
    ``submit_background(url, ...)`` which fires ``run()`` as an asyncio task.
    """

    def __init__(
        self,
        store: "KnowledgeStore",
        http_client: "httpx.AsyncClient",
        notifications: "NotificationStore",
        category_engine: "CategoryEngine",
        qmd_base_url: str = "",
        llm_base_url: str = "",
    ) -> None:
        self._store = store
        self._http_client = http_client
        self._notifications = notifications
        self._category_engine = category_engine
        self._qmd_base_url = qmd_base_url
        self._llm_base_url = llm_base_url

    async def submit(
        self,
        url: str,
        title: str = "",
        text: str = "",
        categories: list[str] | None = None,
        source: str = "unknown",
    ) -> str:
        """Create a pending KnowledgeItem and return its id.

        Does not start the pipeline. Call ``run(item_id)`` or
        ``submit_background(...)`` to actually process the item.
        """
        source_type = resolve_source_type(url)
        monitor_config = dict(_DEFAULT_MONITOR.get(source_type, _DEFAULT_MONITOR["article"]))
        item_id = await self._store.add_item(
            source_type=source_type,
            source_url=url,
            title=title or url,
            author="",
            content=text,
            summary="",
            categories=categories or [],
            tags=[],
            metadata={"ingest_source": source},
            status="pending",
            monitor=monitor_config,
        )
        return item_id

    async def submit_background(
        self,
        url: str,
        title: str = "",
        text: str = "",
        categories: list[str] | None = None,
        source: str = "unknown",
    ) -> str:
        """Submit and immediately fire ``run()`` as a background asyncio task."""
        item_id = await self.submit(url=url, title=title, text=text, categories=categories, source=source)
        asyncio.create_task(self._run_safe(item_id))
        return item_id

    async def _run_safe(self, item_id: str) -> None:
        """Wrapper that catches all exceptions so background tasks never crash silently."""
        try:
            await self.run(item_id)
        except Exception as exc:
            logger.exception("IngestPipeline background task failed for %s: %s", item_id, exc)
            await self._store.update_status(item_id, "error")

    async def run(self, item_id: str) -> None:
        """Execute all pipeline steps for an existing pending item."""
        item = await self._store.get_item(item_id)
        if item is None:
            logger.error("IngestPipeline.run: item %s not found", item_id)
            return

        await self._store.update_status(item_id, "processing")

        try:
            # Step 1: download content if not already provided
            content = item["content"]
            title = item["title"] if item["title"] != item["source_url"] else ""
            author = item["author"]
            metadata = dict(item["metadata"])

            if not content or len(content) < _MIN_CONTENT_CHARS:
                content, title, author, metadata = await self._download(
                    item["source_type"], item["source_url"], title, metadata
                )

            # Step 2: categorise
            categories = item["categories"] or []
            if not categories:
                categories = await self._category_engine.categorise(
                    source_type=item["source_type"],
                    source_url=item["source_url"],
                    title=title or item["source_url"],
                    summary="",
                    metadata=metadata,
                )

            # Step 3: summarise via LLM (best-effort, non-fatal)
            summary = await self._summarise(title, content)

            # Step 4: embed via QMD (best-effort, non-fatal)
            await self._embed(item_id, title, content)

            # Step 5: write final data and mark ready
            await self._store.update_item(
                item_id,
                title=title or item["source_url"],
                author=author,
                content=content,
                summary=summary,
                categories=categories,
                metadata=metadata,
            )
            await self._store.update_status(item_id, "ready")

            # Step 6: notify subscribed agents
            await self._notify(item_id, title, categories)

        except Exception as exc:
            logger.exception("IngestPipeline.run failed for %s: %s", item_id, exc)
            await self._store.update_status(item_id, "error")

    # ------------------------------------------------------------------
    # Download step
    # ------------------------------------------------------------------

    async def _download(
        self, source_type: str, url: str, title: str, metadata: dict
    ) -> tuple[str, str, str, dict]:
        """Download content from the source. Returns (content, title, author, metadata).

        Currently implements article download via HTTP + readability extraction.
        Other source types return empty content so the pipeline can still proceed
        with whatever the caller provided (or mark the item as needing a platform
        adapter that will be added in later build steps).
        """
        if source_type == "article":
            return await self._download_article(url, title, metadata)
        # Placeholder for platform-specific downloaders (reddit, youtube, x, github)
        # added in build steps 3-6. Return empty so the item is stored with
        # status=ready and content="" until a platform adapter fills it.
        return "", title, "", metadata

    async def _download_article(
        self, url: str, title: str, metadata: dict
    ) -> tuple[str, str, str, dict]:
        """Fetch an article URL and extract readable text."""
        resp = await self._http_client.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        content = _extract_text_readability(html)
        if len(content) < _MIN_CONTENT_CHARS:
            logger.warning("Readability extraction returned short content for %s (%d chars)", url, len(content))
            # Screenshot fallback would go here in Phase 2
        # Try to extract title from <title> tag if not provided
        if not title:
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
        return content, title, "", metadata

    # ------------------------------------------------------------------
    # Summarise step
    # ------------------------------------------------------------------

    async def _summarise(self, title: str, content: str) -> str:
        """Request a 2-3 sentence summary from the LLM. Returns empty string on failure."""
        if not self._llm_base_url or not content:
            return ""
        truncated = content[:4000]  # stay within typical context limits
        prompt = (
            f"Summarise the following content in 2-3 sentences. "
            f"Be specific about what the content covers and who it is useful for.\n\n"
            f"Title: {title}\n\nContent:\n{truncated}"
        )
        try:
            resp = await self._http_client.post(
                f"{self._llm_base_url}/generate",
                json={"prompt": prompt, "max_tokens": 150},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("text", data.get("content", "")).strip()
        except Exception as exc:
            logger.warning("Summarise LLM call failed: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Embed step
    # ------------------------------------------------------------------

    async def _embed(self, item_id: str, title: str, content: str) -> None:
        """Send content to QMD for vector embedding into the 'knowledge' collection."""
        if not self._qmd_base_url or not content:
            return
        text_to_embed = f"{title}\n\n{content}"
        # Chunk if content is very long (simple fixed-size chunking)
        chunk_size = 2000
        chunks = [text_to_embed[i:i + chunk_size] for i in range(0, len(text_to_embed), chunk_size)]
        for seq, chunk in enumerate(chunks):
            try:
                await self._http_client.post(
                    f"{self._qmd_base_url}/ingest",
                    json={
                        "collection": "knowledge",
                        "path": f"knowledge/{item_id}/chunk_{seq}",
                        "title": title,
                        "body": chunk,
                    },
                    timeout=60,
                )
            except Exception as exc:
                logger.warning("QMD embed failed for item %s chunk %d: %s", item_id, seq, exc)

    # ------------------------------------------------------------------
    # Notify step
    # ------------------------------------------------------------------

    async def _notify(self, item_id: str, title: str, categories: list[str]) -> None:
        """Emit a notification for agents subscribed to the item's categories."""
        subs = await self._store.subscribers_for_categories(categories)
        if not subs:
            await self._notifications.emit_event(
                "knowledge.item.ready",
                title=f"New knowledge item: {title}",
                message=f"Item {item_id} is ready. Categories: {', '.join(categories) or 'none'}",
            )
            return
        for sub in subs:
            await self._notifications.emit_event(
                "knowledge.item.ready",
                title=f"New knowledge item for {sub['agent_name']}: {title}",
                message=f"Item {item_id} matches subscribed category '{sub['category']}'. auto_ingest={sub['auto_ingest']}",
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_ingest.py -v
```

Expected:
```
tests/test_knowledge_ingest.py::test_resolve_reddit PASSED
tests/test_knowledge_ingest.py::test_resolve_youtube PASSED
tests/test_knowledge_ingest.py::test_resolve_x PASSED
tests/test_knowledge_ingest.py::test_resolve_github PASSED
tests/test_knowledge_ingest.py::test_resolve_article_fallback PASSED
tests/test_knowledge_ingest.py::test_ingest_creates_pending_item PASSED
tests/test_knowledge_ingest.py::test_ingest_article_sets_ready PASSED
tests/test_knowledge_ingest.py::test_ingest_with_text_override PASSED
tests/test_knowledge_ingest.py::test_ingest_notifies_on_ready PASSED
tests/test_knowledge_ingest.py::test_ingest_sets_error_on_failure PASSED
10 passed
```

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos
git add tinyagentos/knowledge_ingest.py tests/test_knowledge_ingest.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "feat(knowledge): add IngestPipeline with URL resolution, article download, and background task"
```

---

### Task 5: MonitorService — poll loop and smart decay

**Files:**
- Create: `tinyagentos/knowledge_monitor.py`
- Create: `tests/test_knowledge_monitor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_knowledge_monitor.py
from __future__ import annotations
import time
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_monitor import MonitorService, compute_next_interval


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    yield s
    await s.close()


# --- Smart decay logic ---

def test_decay_on_no_change():
    """No change detected: multiply interval by decay_rate, floor at 86400."""
    new_interval = compute_next_interval(
        current_interval=3600,
        decay_rate=1.5,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == int(3600 * 1.5)


def test_reset_on_change():
    """Change detected: reset to base_frequency."""
    new_interval = compute_next_interval(
        current_interval=7200,
        decay_rate=1.5,
        changed=True,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == 3600


def test_floor_at_24_hours():
    """Interval must never exceed 86400 seconds (24 hours floor for the next poll gap)."""
    new_interval = compute_next_interval(
        current_interval=80000,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == 86400


def test_stop_after_idle_threshold():
    """After stop_after_days of no change the interval is set to None (stop polling)."""
    new_interval = compute_next_interval(
        current_interval=86400 * 29,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    # After one more decay step interval would be 86400*29*2 which exceeds stop_after_days*86400
    assert new_interval is None


def test_pinned_item_uses_base_frequency():
    """Pinned items always return base_frequency regardless of change."""
    new_interval = compute_next_interval(
        current_interval=86400,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
        pinned=True,
    )
    assert new_interval == 3600


# --- Due-for-poll detection ---

@pytest.mark.asyncio
async def test_items_due_for_poll(store):
    """Items whose last_poll + current_interval <= now should be returned as due."""
    # Item with last_poll far in the past
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/abc",
        title="Thread",
        author="u/tester",
        content="text",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        monitor={"frequency": 3600, "decay_rate": 1.5, "stop_after_days": 30,
                  "pinned": False, "last_poll": time.time() - 7200, "current_interval": 3600},
    )
    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()
    assert any(d["id"] == item_id for d in due)


@pytest.mark.asyncio
async def test_items_not_due_yet(store):
    """Items polled recently should not appear in due list."""
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/xyz",
        title="Recent Thread",
        author="u/tester",
        content="text",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        monitor={"frequency": 3600, "decay_rate": 1.5, "stop_after_days": 30,
                  "pinned": False, "last_poll": time.time(), "current_interval": 3600},
    )
    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()
    assert not any(d["id"] == item_id for d in due)


@pytest.mark.asyncio
async def test_poll_item_updates_monitor_config(store):
    """After a poll, last_poll is updated and current_interval reflects decay."""
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/article",
        title="Article",
        author="",
        content="original content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,
                  "pinned": False, "last_poll": 0, "current_interval": 86400},
    )
    response = AsyncMock()
    response.status_code = 200
    response.text = "original content"  # no change
    response.raise_for_status = AsyncMock()
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=response)

    svc = MonitorService(store=store, http_client=mock_http)
    await svc.poll_item(item_id)

    item = await store.get_item(item_id)
    assert item["monitor"]["last_poll"] > 0
    # No change -> interval decays
    assert item["monitor"]["current_interval"] == int(86400 * 2.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_monitor.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'tinyagentos.knowledge_monitor'`

- [ ] **Step 3: Implement MonitorService**

```python
# tinyagentos/knowledge_monitor.py
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx
    from tinyagentos.knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)

_POLL_LOOP_INTERVAL = 60  # seconds between poll-loop ticks
_MAX_DAILY_INTERVAL = 86400  # 24 hours — polling floor


def compute_next_interval(
    current_interval: int,
    decay_rate: float,
    changed: bool,
    base_frequency: int,
    stop_after_days: int,
    pinned: bool = False,
) -> int | None:
    """Compute the next polling interval after a poll.

    Returns:
        int: new interval in seconds
        None: item should stop being monitored (idle threshold exceeded)
    """
    if pinned:
        return base_frequency

    if changed:
        return base_frequency

    new_interval = int(current_interval * decay_rate)

    # Check if we've exceeded the stop threshold
    if stop_after_days > 0 and new_interval > stop_after_days * _MAX_DAILY_INTERVAL:
        return None

    # Floor: never slower than 24 hours
    return min(new_interval, _MAX_DAILY_INTERVAL)


class MonitorService:
    """Background service that polls monitored KnowledgeItems for changes.

    Start with ``start()`` inside the app lifespan and stop with ``stop()``.
    ``poll_item()`` and ``get_due_items()`` are public for testing.
    """

    def __init__(self, store: "KnowledgeStore", http_client: "httpx.AsyncClient") -> None:
        self._store = store
        self._http_client = http_client
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the 60-second poll loop as a background asyncio task."""
        self._task = asyncio.create_task(self._loop())
        logger.info("MonitorService started")

    async def stop(self) -> None:
        """Cancel the poll loop and wait for it to finish."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MonitorService stopped")

    async def _loop(self) -> None:
        """Main poll loop: runs every 60 seconds."""
        while True:
            try:
                due = await self.get_due_items()
                for item in due:
                    try:
                        await self.poll_item(item["id"])
                    except Exception as exc:
                        logger.warning("poll_item failed for %s: %s", item["id"], exc)
            except Exception as exc:
                logger.warning("MonitorService loop error: %s", exc)
            await asyncio.sleep(_POLL_LOOP_INTERVAL)

    async def get_due_items(self) -> list[dict]:
        """Return items whose next poll time has passed.

        An item is due when ``last_poll + current_interval <= now``.
        Items with ``current_interval == 0`` (files, manual) are excluded.
        Items whose monitor config is missing or empty are excluded.
        """
        now = time.time()
        items = await self._store.list_items(status="ready")
        due = []
        for item in items:
            m = item.get("monitor") or {}
            current_interval = m.get("current_interval", 0)
            last_poll = m.get("last_poll", 0)
            if current_interval <= 0:
                continue
            if last_poll + current_interval <= now:
                due.append(item)
        return due

    async def poll_item(self, item_id: str) -> None:
        """Re-fetch one item, diff against last snapshot, and update monitor config."""
        item = await self._store.get_item(item_id)
        if item is None:
            return

        source_type = item["source_type"]
        monitor = dict(item.get("monitor") or {})

        new_content, changed = await self._fetch_current_content(source_type, item)

        # Record snapshot
        content_hash = hashlib.sha256((new_content or "").encode()).hexdigest()
        old_hash = monitor.get("last_hash", "")
        diff = {"changed": changed, "old_hash": old_hash, "new_hash": content_hash}
        await self._store.add_snapshot(
            item_id,
            content_hash=content_hash,
            diff_json=diff,
            metadata_json={},
        )

        # Update content if changed
        if changed and new_content:
            await self._store.update_item(item_id, content=new_content)

        # Compute next interval
        next_interval = compute_next_interval(
            current_interval=monitor.get("current_interval", monitor.get("frequency", 86400)),
            decay_rate=monitor.get("decay_rate", 1.5),
            changed=changed,
            base_frequency=monitor.get("frequency", 86400),
            stop_after_days=monitor.get("stop_after_days", 14),
            pinned=monitor.get("pinned", False),
        )

        monitor["last_poll"] = time.time()
        monitor["last_hash"] = content_hash
        if next_interval is None:
            monitor["current_interval"] = 0  # stop polling
        else:
            monitor["current_interval"] = next_interval

        await self._store.update_item(item_id, monitor=monitor)

    async def _fetch_current_content(
        self, source_type: str, item: dict
    ) -> tuple[str, bool]:
        """Fetch the current content for an item and determine if it changed.

        Returns (new_content, changed). For source types without a fetcher
        yet (reddit, youtube, x, github), returns ("", False) as a safe
        no-op until platform adapters are added in later build steps.
        """
        if source_type == "article":
            return await self._fetch_article(item)
        # Platform-specific fetchers added in build steps 3-6
        return "", False

    async def _fetch_article(self, item: dict) -> tuple[str, bool]:
        """Re-fetch an article URL and check if content changed."""
        try:
            resp = await self._http_client.get(
                item["source_url"], timeout=30, follow_redirects=True
            )
            resp.raise_for_status()
            new_content = resp.text
            old_content = item.get("content", "")
            changed = new_content.strip() != old_content.strip()
            return new_content, changed
        except Exception as exc:
            logger.warning("Article re-fetch failed for %s: %s", item["source_url"], exc)
            return "", False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_monitor.py -v
```

Expected:
```
tests/test_knowledge_monitor.py::test_decay_on_no_change PASSED
tests/test_knowledge_monitor.py::test_reset_on_change PASSED
tests/test_knowledge_monitor.py::test_floor_at_24_hours PASSED
tests/test_knowledge_monitor.py::test_stop_after_idle_threshold PASSED
tests/test_knowledge_monitor.py::test_pinned_item_uses_base_frequency PASSED
tests/test_knowledge_monitor.py::test_items_due_for_poll PASSED
tests/test_knowledge_monitor.py::test_items_not_due_yet PASSED
tests/test_knowledge_monitor.py::test_poll_item_updates_monitor_config PASSED
8 passed
```

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos
git add tinyagentos/knowledge_monitor.py tests/test_knowledge_monitor.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "feat(knowledge): add MonitorService with 60s poll loop and smart decay"
```

---

### Task 6: API routes — ingest, list, get, delete

**Files:**
- Create: `tinyagentos/routes/knowledge.py`
- Create: `tests/test_knowledge_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_knowledge_routes.py
from __future__ import annotations
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from pathlib import Path
from httpx import ASGITransport, AsyncClient
from tinyagentos.app import create_app
import yaml


@pytest_asyncio.fixture
async def knowledge_client(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    # Init required stores
    await app.state.metrics.init()
    await app.state.notifications.init()
    await app.state.qmd_client.init()
    await app.state.knowledge_store.init()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.knowledge_store.close()
    await app.state.notifications.close()
    await app.state.metrics.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_ingest_returns_item_id(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/test",
        "title": "Test Article",
        "text": "Some pre-provided content that is long enough for testing purposes.",
        "categories": ["Tech"],
        "source": "test",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_list_items_empty(knowledge_client):
    resp = await knowledge_client.get("/api/knowledge/items")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_get_item(knowledge_client):
    # Ingest first
    ingest_resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/get-test",
        "title": "Get Test",
        "text": "Content for get test endpoint.",
        "categories": [],
        "source": "test",
    })
    item_id = ingest_resp.json()["id"]

    resp = await knowledge_client.get(f"/api/knowledge/items/{item_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == item_id
    assert data["source_url"] == "https://example.com/get-test"


@pytest.mark.asyncio
async def test_get_item_not_found(knowledge_client):
    resp = await knowledge_client.get("/api/knowledge/items/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_item(knowledge_client):
    ingest_resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/delete-test",
        "title": "Delete Test",
        "text": "Content for delete test.",
        "categories": [],
        "source": "test",
    })
    item_id = ingest_resp.json()["id"]

    del_resp = await knowledge_client.delete(f"/api/knowledge/items/{item_id}")
    assert del_resp.status_code == 200

    get_resp = await knowledge_client.get(f"/api/knowledge/items/{item_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_items_filter_by_source_type(knowledge_client):
    await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://reddit.com/r/test/comments/abc",
        "title": "Reddit post",
        "text": "Reddit content.",
        "categories": [],
        "source": "test",
    })
    await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/article",
        "title": "Article",
        "text": "Article content.",
        "categories": [],
        "source": "test",
    })
    resp = await knowledge_client.get("/api/knowledge/items?source_type=reddit")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["source_type"] == "reddit" for i in items)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_routes.py -v 2>&1 | head -30
```

Expected: `AttributeError: 'State' object has no attribute 'knowledge_store'` (routes not wired yet)

- [ ] **Step 3: Implement the routes file**

```python
# tinyagentos/routes/knowledge.py
"""API routes for the Knowledge Base Service.

All routes live under /api/knowledge/. The router reads state from
``request.app.state``:

- ``knowledge_store``   — KnowledgeStore instance
- ``ingest_pipeline``   — IngestPipeline instance
- ``http_client``       — shared httpx.AsyncClient
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class IngestRequest(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    categories: list[str] = []
    source: str = "unknown"


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"  # "keyword" or "semantic"
    limit: int = 20


class SubscriptionRequest(BaseModel):
    agent_name: str
    category: str
    auto_ingest: bool = False


class RuleRequest(BaseModel):
    pattern: str
    match_on: str
    category: str
    priority: int = 0


# ------------------------------------------------------------------
# Ingest
# ------------------------------------------------------------------

@router.post("/api/knowledge/ingest")
async def ingest(request: Request, body: IngestRequest):
    """Submit a URL or pre-provided text for ingest.

    Returns immediately with the new item id and status='pending'.
    The pipeline runs in the background.
    """
    pipeline = request.app.state.ingest_pipeline
    try:
        item_id = await pipeline.submit_background(
            url=body.url,
            title=body.title,
            text=body.text,
            categories=body.categories,
            source=body.source,
        )
        return {"id": item_id, "status": "pending"}
    except Exception as exc:
        logger.exception("ingest failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------
# Items — CRUD
# ------------------------------------------------------------------

@router.get("/api/knowledge/items")
async def list_items(
    request: Request,
    source_type: str | None = None,
    status: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List knowledge items with optional filters."""
    store = request.app.state.knowledge_store
    items = await store.list_items(
        source_type=source_type,
        status=status,
        category=category,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "count": len(items)}


@router.get("/api/knowledge/items/{item_id}")
async def get_item(request: Request, item_id: str):
    """Fetch a single knowledge item by id."""
    store = request.app.state.knowledge_store
    item = await store.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return item


@router.delete("/api/knowledge/items/{item_id}")
async def delete_item(request: Request, item_id: str):
    """Delete a knowledge item."""
    store = request.app.state.knowledge_store
    deleted = await store.delete_item(item_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": item_id}


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------

@router.post("/api/knowledge/search")
async def search(request: Request, body: SearchRequest):
    """Search the knowledge base by keyword (FTS5) or semantic (QMD vectors)."""
    store = request.app.state.knowledge_store
    if body.mode == "semantic":
        http_client = request.app.state.http_client
        qmd_base = request.app.state.qmd_client.base_url
        try:
            resp = await http_client.post(
                f"{qmd_base}/vsearch",
                json={"query": body.query, "limit": body.limit, "collection": "knowledge"},
                timeout=60,
            )
            resp.raise_for_status()
            return {"results": resp.json().get("results", []), "mode": "semantic"}
        except Exception as exc:
            logger.warning("QMD vsearch failed, falling back to FTS: %s", exc)
    results = await store.search_fts(body.query, limit=body.limit)
    return {"results": results, "mode": "keyword"}


# ------------------------------------------------------------------
# Snapshots
# ------------------------------------------------------------------

@router.get("/api/knowledge/items/{item_id}/snapshots")
async def list_snapshots(request: Request, item_id: str, limit: int = 20):
    """List monitoring snapshots for an item."""
    store = request.app.state.knowledge_store
    item = await store.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    snapshots = await store.list_snapshots(item_id, limit=limit)
    return {"snapshots": snapshots}


# ------------------------------------------------------------------
# Category rules
# ------------------------------------------------------------------

@router.get("/api/knowledge/rules")
async def list_rules(request: Request):
    """List all category rules."""
    store = request.app.state.knowledge_store
    return {"rules": await store.list_rules()}


@router.post("/api/knowledge/rules")
async def create_rule(request: Request, body: RuleRequest):
    """Create a new category rule."""
    store = request.app.state.knowledge_store
    rule_id = await store.add_rule(
        pattern=body.pattern,
        match_on=body.match_on,
        category=body.category,
        priority=body.priority,
    )
    return {"id": rule_id, "status": "created"}


@router.delete("/api/knowledge/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: int):
    """Delete a category rule."""
    store = request.app.state.knowledge_store
    deleted = await store.delete_rule(rule_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": rule_id}


# ------------------------------------------------------------------
# Agent subscriptions
# ------------------------------------------------------------------

@router.get("/api/knowledge/subscriptions")
async def list_subscriptions(request: Request, agent_name: str | None = None):
    """List agent knowledge subscriptions."""
    store = request.app.state.knowledge_store
    return {"subscriptions": await store.list_subscriptions(agent_name=agent_name)}


@router.post("/api/knowledge/subscriptions")
async def set_subscription(request: Request, body: SubscriptionRequest):
    """Upsert an agent subscription for a category."""
    store = request.app.state.knowledge_store
    await store.set_subscription(
        agent_name=body.agent_name,
        category=body.category,
        auto_ingest=body.auto_ingest,
    )
    return {"status": "ok"}


@router.delete("/api/knowledge/subscriptions")
async def delete_subscription(
    request: Request, agent_name: str, category: str
):
    """Remove an agent subscription."""
    store = request.app.state.knowledge_store
    deleted = await store.delete_subscription(agent_name=agent_name, category=category)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted"}
```

- [ ] **Step 4: Wire store, pipeline, and router into `app.py`**

Open `/home/jay/tinyagentos/tinyagentos/app.py` and make three edits.

**Edit 1** — add imports after the `from tinyagentos.skills import SkillStore` import line:

```python
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_ingest import IngestPipeline
from tinyagentos.knowledge_categories import CategoryEngine
from tinyagentos.knowledge_monitor import MonitorService
```

**Edit 2** — add store/pipeline/monitor instantiation after the `skills = SkillStore(...)` line:

```python
    knowledge_store = KnowledgeStore(
        data_dir / "knowledge.db",
        media_dir=data_dir / "knowledge-media",
    )
    knowledge_category_engine = CategoryEngine(
        store=knowledge_store,
        http_client=http_client,
        llm_url=config.backends[0].get("url", "") if config.backends else "",
    )
    knowledge_ingest = IngestPipeline(
        store=knowledge_store,
        http_client=http_client,
        notifications=notif_store,
        category_engine=knowledge_category_engine,
        qmd_base_url=config.qmd.get("url", "http://localhost:7832"),
        llm_url=config.backends[0].get("url", "") if config.backends else "",
    )
    knowledge_monitor = MonitorService(store=knowledge_store, http_client=http_client)
```

**Edit 3** — add to the lifespan `async with` block, after `await skills.init()`:

```python
        await knowledge_store.init()
        app.state.knowledge_store = knowledge_store
        app.state.ingest_pipeline = knowledge_ingest
        app.state.knowledge_monitor = knowledge_monitor
        await knowledge_monitor.start()
```

And in the shutdown section (after `await skills.close()`):

```python
        await knowledge_monitor.stop()
        await knowledge_store.close()
```

**Edit 4** — add the state assignments in the eager section (after `app.state.skills = skills`):

```python
    app.state.knowledge_store = knowledge_store
    app.state.ingest_pipeline = knowledge_ingest
    app.state.knowledge_monitor = knowledge_monitor
```

**Edit 5** — include the router. After the `from tinyagentos.routes.skills import router as skills_router` block add:

```python
    from tinyagentos.routes.knowledge import router as knowledge_router
    app.include_router(knowledge_router)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_routes.py -v
```

Expected:
```
tests/test_knowledge_routes.py::test_ingest_returns_item_id PASSED
tests/test_knowledge_routes.py::test_list_items_empty PASSED
tests/test_knowledge_routes.py::test_get_item PASSED
tests/test_knowledge_routes.py::test_get_item_not_found PASSED
tests/test_knowledge_routes.py::test_delete_item PASSED
tests/test_knowledge_routes.py::test_list_items_filter_by_source_type PASSED
6 passed
```

- [ ] **Step 6: Run the full test suite to verify no regressions**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/ -v --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -20
```

Expected: all pre-existing tests still pass; new knowledge tests pass. Any failure will name a specific test — investigate before committing.

- [ ] **Step 7: Commit**

```bash
cd /home/jay/tinyagentos
git add tinyagentos/routes/knowledge.py tinyagentos/app.py tests/test_knowledge_routes.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "feat(knowledge): add API routes and wire KnowledgeStore + IngestPipeline into app"
```

---

### Task 7: API routes — search, snapshots, rules, subscriptions

**Files:**
- Modify: `tests/test_knowledge_routes.py` (add tests for remaining endpoints)

The route implementations for these endpoints already exist in `tinyagentos/routes/knowledge.py` from Task 6. This task covers their tests.

- [ ] **Step 1: Write failing tests for the remaining endpoints**

Append to `tests/test_knowledge_routes.py`:

```python
@pytest.mark.asyncio
async def test_search_keyword(knowledge_client):
    # Ingest an item first and run the pipeline so it reaches 'ready' with content
    from tinyagentos.knowledge_store import KnowledgeStore
    # Access the store directly through the app's state is not easy in client fixture.
    # Instead: submit via API, the item may still be pending, but FTS search works on content.
    await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/asyncio-guide",
        "title": "Asyncio Guide",
        "text": "asyncio event loop coroutine await gather",
        "categories": ["Tech"],
        "source": "test",
    })
    resp = await knowledge_client.post("/api/knowledge/search", json={
        "query": "asyncio",
        "mode": "keyword",
        "limit": 10,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["mode"] == "keyword"


@pytest.mark.asyncio
async def test_list_snapshots_empty(knowledge_client):
    ingest_resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/snap-test",
        "title": "Snapshot Test",
        "text": "Content.",
        "categories": [],
        "source": "test",
    })
    item_id = ingest_resp.json()["id"]
    resp = await knowledge_client.get(f"/api/knowledge/items/{item_id}/snapshots")
    assert resp.status_code == 200
    assert resp.json()["snapshots"] == []


@pytest.mark.asyncio
async def test_create_and_list_rules(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/rules", json={
        "pattern": "LocalLLaMA",
        "match_on": "subreddit",
        "category": "AI/ML",
        "priority": 10,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"

    list_resp = await knowledge_client.get("/api/knowledge/rules")
    assert list_resp.status_code == 200
    rules = list_resp.json()["rules"]
    assert any(r["category"] == "AI/ML" for r in rules)


@pytest.mark.asyncio
async def test_delete_rule(knowledge_client):
    create_resp = await knowledge_client.post("/api/knowledge/rules", json={
        "pattern": "temp*",
        "match_on": "source_type",
        "category": "Temp",
        "priority": 0,
    })
    rule_id = create_resp.json()["id"]
    del_resp = await knowledge_client.delete(f"/api/knowledge/rules/{rule_id}")
    assert del_resp.status_code == 200

    list_resp = await knowledge_client.get("/api/knowledge/rules")
    assert not any(r["id"] == rule_id for r in list_resp.json()["rules"])


@pytest.mark.asyncio
async def test_create_and_list_subscriptions(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/subscriptions", json={
        "agent_name": "research-agent",
        "category": "AI/ML",
        "auto_ingest": True,
    })
    assert resp.status_code == 200

    list_resp = await knowledge_client.get("/api/knowledge/subscriptions?agent_name=research-agent")
    assert list_resp.status_code == 200
    subs = list_resp.json()["subscriptions"]
    assert len(subs) == 1
    assert subs[0]["category"] == "AI/ML"
    assert subs[0]["auto_ingest"] is True


@pytest.mark.asyncio
async def test_delete_subscription(knowledge_client):
    await knowledge_client.post("/api/knowledge/subscriptions", json={
        "agent_name": "dev-agent",
        "category": "Development",
        "auto_ingest": False,
    })
    del_resp = await knowledge_client.delete(
        "/api/knowledge/subscriptions?agent_name=dev-agent&category=Development"
    )
    assert del_resp.status_code == 200

    list_resp = await knowledge_client.get("/api/knowledge/subscriptions?agent_name=dev-agent")
    assert list_resp.json()["subscriptions"] == []
```

- [ ] **Step 2: Run all knowledge route tests**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_routes.py -v
```

Expected: all 12 tests pass.

- [ ] **Step 3: Commit**

```bash
cd /home/jay/tinyagentos
git add tests/test_knowledge_routes.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "test(knowledge): add route tests for search, snapshots, rules, and subscriptions"
```

---

### Task 8: Wire MonitorService start/stop into lifespan and smoke test

**Files:**
- Modify: `tinyagentos/app.py` (verify lifespan wiring from Task 6 is correct)
- Modify: `tests/test_knowledge_routes.py` (add monitor wiring smoke test)

This task validates the full startup/shutdown path and that the monitor service does not break the app lifespan.

- [ ] **Step 1: Write a smoke test for app startup with knowledge stores**

Append to `tests/test_knowledge_routes.py`:

```python
@pytest.mark.asyncio
async def test_knowledge_store_in_app_state(knowledge_client):
    """Verify knowledge_store is accessible on app state after startup."""
    # The client fixture initialises the store — just verify the list endpoint works
    resp = await knowledge_client.get("/api/knowledge/items")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ingest_pipeline_in_app_state(knowledge_client):
    """Verify ingest_pipeline is accessible and returns a valid item id."""
    resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/smoke-test",
        "title": "Smoke Test",
        "text": "Smoke test content.",
        "categories": [],
        "source": "smoke",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["id"]) == 36  # UUID length
```

- [ ] **Step 2: Verify the app.py edits from Task 6 are correct**

Check the lifespan block contains all four knowledge lines. Run:

```bash
cd /home/jay/tinyagentos
python -c "from tinyagentos.app import create_app; print('import ok')"
```

Expected: `import ok` — no ImportError.

- [ ] **Step 3: Run full knowledge test suite**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_store.py tests/test_knowledge_ingest.py tests/test_knowledge_monitor.py tests/test_knowledge_categories.py tests/test_knowledge_routes.py -v
```

Expected: all tests pass (exact count depends on prior tasks: approximately 38 tests).

- [ ] **Step 4: Run regression check**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/ --ignore=tests/e2e --ignore=tests/integration -q 2>&1 | tail -10
```

Expected: `N passed` with no failures. If there are failures, the error message will identify the broken test; fix before committing.

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos
git add tests/test_knowledge_routes.py tinyagentos/app.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "test(knowledge): smoke tests for app state wiring and full regression check"
```

---

### Task 9: `IngestPipeline` — LLM summarise and QMD embed integration tests

**Files:**
- Modify: `tests/test_knowledge_ingest.py` (add integration-style tests with mocked LLM + QMD)

- [ ] **Step 1: Write integration tests with mocked HTTP responses**

Append to `tests/test_knowledge_ingest.py`:

```python
@pytest.mark.asyncio
async def test_summarise_called_when_llm_url_set(store):
    """When llm_base_url is set, _summarise should be called and stored."""
    from tinyagentos.knowledge_ingest import IngestPipeline

    llm_response = AsyncMock()
    llm_response.status_code = 200
    llm_response.json = MagicMock(return_value={"text": "This is a generated summary."})
    llm_response.raise_for_status = MagicMock()

    article_response = AsyncMock()
    article_response.status_code = 200
    article_response.text = "<html><body><p>Long enough article body text content here for testing purposes.</p></body></html>"
    article_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    # First call is article fetch, second call is LLM summarise
    mock_http.get = AsyncMock(return_value=article_response)
    mock_http.post = AsyncMock(return_value=llm_response)

    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=["Tech"])

    pipeline = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="",  # disable embed for this test
        llm_base_url="http://localhost:8080",
    )

    item_id = await pipeline.submit(
        url="https://example.com/summarise-test",
        title="",
        text="",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)

    item = await store.get_item(item_id)
    assert item["summary"] == "This is a generated summary."


@pytest.mark.asyncio
async def test_embed_called_when_qmd_url_set(store):
    """When qmd_base_url is set, the /ingest endpoint should be called with collection=knowledge."""
    from tinyagentos.knowledge_ingest import IngestPipeline

    qmd_response = AsyncMock()
    qmd_response.status_code = 200
    qmd_response.raise_for_status = AsyncMock()

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=Exception("no HTTP in this test"))
    mock_http.post = AsyncMock(return_value=qmd_response)

    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=[])

    pipeline = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="http://localhost:7832",
        llm_base_url="",
    )

    item_id = await pipeline.submit(
        url="https://example.com/embed-test",
        title="Embed Test",
        text="Content long enough to trigger embedding pipeline call here.",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)

    # Verify /ingest was called on the QMD base URL
    calls = [str(call) for call in mock_http.post.call_args_list]
    assert any("ingest" in c for c in calls)


@pytest.mark.asyncio
async def test_categories_from_caller_are_preserved(store):
    """When categories are provided at submit time, they bypass the engine."""
    from tinyagentos.knowledge_ingest import IngestPipeline

    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=["Wrong"])  # should not be called

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=Exception("no HTTP"))

    pipeline = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="",
        llm_base_url="",
    )

    item_id = await pipeline.submit(
        url="https://example.com/precategorised",
        title="Pre-categorised",
        text="Content long enough for the pipeline to keep.",
        categories=["AI/ML", "Rockchip"],
        source="test",
    )
    await pipeline.run(item_id)

    cat_engine.categorise.assert_not_awaited()
    item = await store.get_item(item_id)
    assert "AI/ML" in item["categories"]
    assert "Rockchip" in item["categories"]
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/test_knowledge_ingest.py -v
```

Expected: all 13 tests pass.

- [ ] **Step 3: Commit**

```bash
cd /home/jay/tinyagentos
git add tests/test_knowledge_ingest.py
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "test(knowledge): add LLM summarise and QMD embed integration tests for IngestPipeline"
```

---

### Task 10: Final regression and cleanup

**Files:**
- Verify: all files created/modified in Tasks 1-9

- [ ] **Step 1: Run the complete test suite (excluding e2e)**

```bash
cd /home/jay/tinyagentos
python -m pytest tests/ --ignore=tests/e2e --ignore=tests/integration -v 2>&1 | tail -30
```

Expected: all tests pass. If any existing tests fail, the error will identify the specific test and module — fix the regression before proceeding.

- [ ] **Step 2: Verify all five new modules import cleanly**

```bash
cd /home/jay/tinyagentos
python -c "
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_ingest import IngestPipeline, resolve_source_type
from tinyagentos.knowledge_monitor import MonitorService, compute_next_interval
from tinyagentos.knowledge_categories import CategoryEngine
from tinyagentos.routes.knowledge import router
print('all imports ok')
"
```

Expected: `all imports ok`

- [ ] **Step 3: Verify the app starts without error**

```bash
cd /home/jay/tinyagentos
python -c "
from tinyagentos.app import create_app
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    app = create_app(data_dir=pathlib.Path(d))
    print('app created ok, routes:', len(app.routes))
"
```

Expected: `app created ok, routes: N` with N greater than the pre-existing count (new knowledge routes added).

- [ ] **Step 4: Check all knowledge routes are registered**

```bash
cd /home/jay/tinyagentos
python -c "
from tinyagentos.app import create_app
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    app = create_app(data_dir=pathlib.Path(d))
    knowledge_routes = [r.path for r in app.routes if hasattr(r, 'path') and '/knowledge/' in r.path]
    for r in sorted(knowledge_routes):
        print(r)
"
```

Expected output includes:
```
/api/knowledge/ingest
/api/knowledge/items
/api/knowledge/items/{item_id}
/api/knowledge/items/{item_id}/snapshots
/api/knowledge/rules
/api/knowledge/rules/{rule_id}
/api/knowledge/search
/api/knowledge/subscriptions
```

- [ ] **Step 5: Final commit**

```bash
cd /home/jay/tinyagentos
git add -p  # review any remaining unstaged changes
git -c user.name=jaylfc -c user.email=jaylfc25@gmail.com commit -m "chore(knowledge): verify all routes registered and full test suite passes" --allow-empty
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by task |
|---|---|
| KnowledgeStore (SQLite + FTS5) | Task 1, 2 |
| knowledge_snapshots table | Task 1 |
| category_rules table | Task 1, 2 |
| agent_knowledge_subscriptions table | Task 1, 2 |
| media_dir at data/knowledge-media/ | Task 1 (KnowledgeStore._post_init) |
| IngestPipeline — URL resolution | Task 4 (resolve_source_type) |
| IngestPipeline — article download | Task 4 (_download_article) |
| IngestPipeline — summarise via LLM | Task 4 (_summarise), Task 9 |
| IngestPipeline — embed via QMD | Task 4 (_embed), Task 9 |
| IngestPipeline — store + status=ready | Task 4 (run) |
| IngestPipeline — notify subscribed agents | Task 4 (_notify) |
| API returns pending immediately | Task 6 (submit_background) |
| MonitorService 60s poll loop | Task 5 |
| Smart decay — no change multiplies interval | Task 5 (compute_next_interval) |
| Smart decay — change resets to base | Task 5 |
| Smart decay — floor at 24 hours | Task 5 |
| Smart decay — stop after idle threshold | Task 5 |
| Smart decay — pinned items exempt | Task 5 |
| CategoryEngine — glob rule matching | Task 3 |
| CategoryEngine — LLM fallback for unmatched | Task 3 |
| CategoryEngine — LLM skipped when rules match | Task 3 |
| /api/knowledge/ingest endpoint | Task 6 |
| /api/knowledge/items (list, get, delete) | Task 6 |
| /api/knowledge/search (keyword + semantic) | Task 7 |
| /api/knowledge/items/{id}/snapshots | Task 7 |
| /api/knowledge/rules (CRUD) | Task 7 |
| /api/knowledge/subscriptions (CRUD) | Task 7 |
| Wired into create_app + lifespan | Task 6, 8 |
| BaseStore pattern | Task 1 (KnowledgeStore extends BaseStore) |
| aiosqlite for all DB access | All store tasks |
| httpx for HTTP calls | Task 4, 5 |
| FastAPI APIRouter | Task 6 |

**Platform-specific downloaders (Reddit, YouTube, X, GitHub):** These are not in Build Step 1 scope per the spec's Build Order. The IngestPipeline has stubs that return empty content for those types, so items can still be stored and will be filled in by platform adapters in build steps 3-6. This is intentional and correctly scoped.

**Screenshot fallback (Step 2b):** Documented in the spec as a fallback for failed readability extraction. The IngestPipeline has a comment at the fallback point (`# Screenshot fallback would go here in Phase 2`) but does not implement it — this is intentional for Build Step 1. The quality threshold check is implemented.

**Placeholder scan:** No TBD, TODO, or "implement later" language in any code blocks. All method signatures are consistent across tasks. `resolve_source_type` is defined in `knowledge_ingest.py` and imported consistently in tests. `compute_next_interval` is defined in `knowledge_monitor.py` and imported in the monitor test. `_llm_categorise` is defined as an instance method and mocked correctly in `test_no_rule_match_calls_llm_fallback`.

**Type consistency check:**
- `store.add_item(...)` returns `str` (item id) — used consistently in all tests and pipeline
- `store.get_item(item_id)` returns `dict | None` — checked with `is None` throughout
- `store.list_items(...)` returns `list[dict]` — iterated in monitor and routes
- `pipeline.submit(...)` returns `str` — stored as `item_id` in all callers
- `pipeline.submit_background(...)` returns `str` — used in route handler
- `pipeline.run(item_id)` returns `None` — no return value assumed anywhere
- `compute_next_interval(...)` returns `int | None` — None check present in `poll_item`
- `engine.categorise(...)` returns `list[str]` — stored as `categories` list
- `IngestPipeline.__init__` parameter is `llm_base_url` — consistent with `_summarise` usage. Note: `app.py` wiring uses `llm_url=` keyword — **fix**: the parameter name in `IngestPipeline.__init__` is `llm_base_url` but the `app.py` wiring in Task 6 Step 4 says `llm_url=`. The correct kwarg is `llm_base_url=`. The Task 6 Step 4 edit block must use `llm_base_url=config.backends[0].get("url", "") if config.backends else ""`.
