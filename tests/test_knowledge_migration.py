from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.knowledge_store import KnowledgeStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_old_schema_db(db_path: Path) -> None:
    """Create a knowledge.db with the pre-user_id schema (simulates a bricked install)."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
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
    """)
    conn.commit()
    conn.close()


def _get_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_items)").fetchall()}
    conn.close()
    return cols


def _get_indexes(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(knowledge_items)").fetchall()}
    conn.close()
    return indexes


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migration_adds_user_id_to_old_db(tmp_path):
    """init() must succeed on an old-shape DB and add user_id + index."""
    db_path = tmp_path / "knowledge.db"
    _create_old_schema_db(db_path)

    # Confirm pre-condition: no user_id column
    assert "user_id" not in _get_columns(db_path)

    store = KnowledgeStore(db_path, media_dir=tmp_path / "media")
    await store.init()
    await store.close()

    cols = _get_columns(db_path)
    assert "user_id" in cols, "user_id column must exist after init"

    indexes = _get_indexes(db_path)
    assert "idx_ki_user_id" in indexes, "idx_ki_user_id must exist after init"


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    """A second init() on an already-migrated DB must be a no-op (no error)."""
    db_path = tmp_path / "knowledge.db"
    _create_old_schema_db(db_path)

    store = KnowledgeStore(db_path, media_dir=tmp_path / "media")
    await store.init()
    await store.close()

    # Second init -- must not raise
    store2 = KnowledgeStore(db_path, media_dir=tmp_path / "media")
    await store2.init()
    await store2.close()

    assert "user_id" in _get_columns(db_path)
    assert "idx_ki_user_id" in _get_indexes(db_path)


@pytest.mark.asyncio
async def test_fresh_db_has_user_id_column_and_index(tmp_path):
    """A brand-new DB must also have user_id + index after init."""
    db_path = tmp_path / "knowledge_fresh.db"

    store = KnowledgeStore(db_path, media_dir=tmp_path / "media")
    await store.init()
    await store.close()

    assert "user_id" in _get_columns(db_path)
    assert "idx_ki_user_id" in _get_indexes(db_path)
