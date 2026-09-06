import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyagentos.agent_db import find_agent, get_agent_db, get_agent_summaries, QMD_CACHE_DIR


def _make_qmd_db(db_path: Path) -> None:
    """Create a minimal QMD-compatible SQLite database with content_vectors."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL,
            hash TEXT NOT NULL, created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(collection, path)
        )
    """)
    conn.execute("""
        CREATE TABLE content_vectors (
            hash TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0,
            pos INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL,
            embedded_at TEXT NOT NULL,
            PRIMARY KEY (hash, seq)
        )
    """)
    conn.execute("CREATE TABLE store_collections (name TEXT PRIMARY KEY, path TEXT NOT NULL, pattern TEXT NOT NULL DEFAULT '**/*.md')")
    conn.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(filepath, title, body, tokenize='porter unicode61')")
    conn.execute("INSERT INTO content_vectors VALUES ('h1', 0, 0, 'test-embed', '2026-05-01')")
    conn.execute("INSERT INTO content_vectors VALUES ('h2', 0, 0, 'test-embed', '2026-05-02')")
    conn.execute("INSERT INTO content_vectors VALUES ('h3', 0, 0, 'test-embed', '2026-05-03')")
    conn.commit()
    conn.close()


def _make_empty_qmd_db(db_path: Path) -> None:
    """Create a QMD database with schema but no vectors."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL,
            hash TEXT NOT NULL, created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(collection, path)
        )
    """)
    conn.execute("""
        CREATE TABLE content_vectors (
            hash TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0,
            pos INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL,
            embedded_at TEXT NOT NULL,
            PRIMARY KEY (hash, seq)
        )
    """)
    conn.execute("CREATE TABLE store_collections (name TEXT PRIMARY KEY, path TEXT NOT NULL, pattern TEXT NOT NULL DEFAULT '**/*.md')")
    conn.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(filepath, title, body, tokenize='porter unicode61')")
    conn.commit()
    conn.close()


class FakeConfig:
    """Minimal config stub with an agents list."""
    def __init__(self, agents):
        self.agents = agents


class TestFindAgent:
    def test_finds_existing_agent(self):
        config = FakeConfig([
            {"name": "alpha", "host": "10.0.0.1"},
            {"name": "beta", "host": "10.0.0.2"},
        ])
        result = find_agent(config, "beta")
        assert result == {"name": "beta", "host": "10.0.0.2"}

    def test_returns_none_for_missing_agent(self):
        config = FakeConfig([
            {"name": "alpha", "host": "10.0.0.1"},
        ])
        assert find_agent(config, "gamma") is None

    def test_returns_none_for_empty_agents(self):
        config = FakeConfig([])
        assert find_agent(config, "any") is None

    def test_returns_first_match_on_duplicate_names(self):
        config = FakeConfig([
            {"name": "dup", "host": "10.0.0.1"},
            {"name": "dup", "host": "10.0.0.2"},
        ])
        result = find_agent(config, "dup")
        assert result["host"] == "10.0.0.1"


class TestGetAgentDb:
    def test_opens_db_with_explicit_path(self, tmp_path):
        db_path = tmp_path / "custom.sqlite"
        _make_qmd_db(db_path)
        agent = {"name": "a1", "qmd_db_path": str(db_path)}
        db = get_agent_db(agent)
        assert db is not None
        assert db.db_path == db_path

    def test_falls_back_to_cache_dir(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        db_path = cache_dir / "myindex.sqlite"
        _make_qmd_db(db_path)
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", cache_dir)
        agent = {"name": "a1", "qmd_index": "myindex"}
        db = get_agent_db(agent)
        assert db is not None
        assert db.db_path == db_path

    def test_default_index_name_is_index(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        db_path = cache_dir / "index.sqlite"
        _make_qmd_db(db_path)
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", cache_dir)
        agent = {"name": "a1"}
        db = get_agent_db(agent)
        assert db is not None
        assert db.db_path == db_path

    def test_returns_none_when_file_missing(self, tmp_path):
        agent = {"name": "a1", "qmd_db_path": str(tmp_path / "nope.sqlite")}
        assert get_agent_db(agent) is None

    def test_returns_none_when_cache_dir_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", tmp_path / "nonexistent")
        agent = {"name": "a1", "qmd_index": "ghost"}
        assert get_agent_db(agent) is None

    def test_explicit_path_takes_priority_over_index(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        explicit_path = tmp_path / "explicit.sqlite"
        _make_qmd_db(explicit_path)
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", cache_dir)
        agent = {"name": "a1", "qmd_db_path": str(explicit_path), "qmd_index": "wrong"}
        db = get_agent_db(agent)
        assert db is not None
        assert db.db_path == explicit_path


class TestGetAgentSummaries:
    def test_returns_summaries_for_all_agents(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _make_qmd_db(cache_dir / "idx1.sqlite")
        _make_qmd_db(cache_dir / "idx2.sqlite")
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", cache_dir)
        config = FakeConfig([
            {"name": "a1", "host": "10.0.0.1", "qmd_index": "idx1", "color": "#f00"},
            {"name": "a2", "host": "10.0.0.2", "qmd_index": "idx2", "color": "#0f0"},
        ])
        summaries = get_agent_summaries(config)
        assert len(summaries) == 2
        assert summaries[0]["name"] == "a1"
        assert summaries[0]["status"] == "ok"
        assert summaries[0]["vectors"] == 3
        assert summaries[0]["last_embedded"] == "2026-05-03"
        assert summaries[1]["name"] == "a2"
        assert summaries[1]["status"] == "ok"

    def test_error_status_when_db_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", tmp_path / "missing")
        config = FakeConfig([
            {"name": "a1", "host": "10.0.0.1", "qmd_index": "ghost"},
        ])
        summaries = get_agent_summaries(config)
        assert summaries[0]["status"] == "error"
        assert summaries[0]["vectors"] == 0
        assert summaries[0]["last_embedded"] is None

    def test_empty_agents_returns_empty_list(self):
        config = FakeConfig([])
        assert get_agent_summaries(config) == []

    def test_defaults_for_optional_fields(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _make_qmd_db(cache_dir / "index.sqlite")
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", cache_dir)
        config = FakeConfig([
            {"name": "bare"},
        ])
        summaries = get_agent_summaries(config)
        assert len(summaries) == 1
        s = summaries[0]
        assert s["name"] == "bare"
        assert s["host"] == ""
        assert s["qmd_index"] == ""
        assert s["color"] == "#888"
        assert s["status"] == "ok"

    def test_mixed_healthy_and_missing_agents(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _make_qmd_db(cache_dir / "good.sqlite")
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", cache_dir)
        config = FakeConfig([
            {"name": "healthy", "qmd_index": "good"},
            {"name": "broken", "qmd_index": "missing"},
        ])
        summaries = get_agent_summaries(config)
        assert summaries[0]["status"] == "ok"
        assert summaries[0]["vectors"] == 3
        assert summaries[1]["status"] == "error"
        assert summaries[1]["vectors"] == 0
        assert summaries[1]["last_embedded"] is None

    def test_empty_vectors_returns_none_last_embedded(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        _make_empty_qmd_db(cache_dir / "empty.sqlite")
        monkeypatch.setattr("tinyagentos.agent_db.QMD_CACHE_DIR", cache_dir)
        config = FakeConfig([
            {"name": "empty", "qmd_index": "empty"},
        ])
        summaries = get_agent_summaries(config)
        assert summaries[0]["status"] == "ok"
        assert summaries[0]["vectors"] == 0
        assert summaries[0]["last_embedded"] is None
