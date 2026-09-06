"""Tests for storage engine selection (SQLite vs Postgres)."""
import aiosqlite
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from tinyagentos.base_store import BaseStore, Engine


class TestStore(BaseStore):
    """Test store implementation."""
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS test_data (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
    """
    MIGRATIONS = []


class TestStoreWithMigrations(BaseStore):
    """Test store with migrations."""
    SCHEMA = ""
    MIGRATIONS = [
        (1, "CREATE TABLE IF NOT EXISTS migrated (id INTEGER PRIMARY KEY, data TEXT)"),
    ]


class TestStorePostgres(BaseStore):
    """Test store using Postgres engine."""
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS pg_test_data (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
    """
    MIGRATIONS = []
    ENGINE = Engine.POSTGRES


@pytest.mark.asyncio
class TestEngineSelection:
    """Tests for storage engine selection."""

    async def test_default_engine_is_sqlite(self, tmp_path):
        """Test that stores default to SQLite engine."""
        store = TestStore(tmp_path / "test.db")
        assert store.engine == Engine.SQLITE

    async def test_explicit_sqlite_engine(self, tmp_path):
        """Test that explicit SQLite engine works."""
        store = TestStore(tmp_path / "test.db", engine=Engine.SQLITE)
        assert store.engine == Engine.SQLITE
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='test_data'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "test_data"
        finally:
            await store.close()

    async def test_postgres_engine_selected(self, tmp_path):
        """Test that Postgres engine can be selected."""
        store = TestStorePostgres(tmp_path / "test.pg")
        assert store.engine == Engine.POSTGRES

    async def test_postgres_fails_when_unavailable(self, tmp_path):
        """Test that Postgres startup fails loudly when unavailable."""
        store = TestStorePostgres(tmp_path / "test.pg")
        with pytest.raises(NotImplementedError, match="Postgres engine not yet implemented"):
            await store.init()

    async def test_sqlite_fallback_when_no_engine_specified(self, tmp_path):
        """Test that SQLite remains the default and works when no engine is configured."""
        store = TestStore(tmp_path / "test.db")
        assert store.engine == Engine.SQLITE
        
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='test_data'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "test_data"
        finally:
            await store.close()


@pytest.mark.asyncio
class TestPostgresIntegration:
    """Integration tests for Postgres engine behavior."""

    async def test_postgres_engine_fails_silently_if_not_implemented(self, tmp_path):
        """Test that selecting Postgres but having it unimplemented raises NotImplementedError."""
        store = TestStorePostgres(tmp_path / "test.pg")
        
        with pytest.raises(NotImplementedError, match="Postgres engine not yet implemented"):
            await store.init()

    async def test_sqlite_engine_works_normally(self, tmp_path):
        """Test that SQLite engine works normally (baseline)."""
        store = TestStore(tmp_path / "test.db")
        
        await store.init()
        try:
            cursor = await store._db.execute("INSERT INTO test_data (name) VALUES (?)", ("test",))
            await store._db.commit()
            
            cursor = await store._db.execute("SELECT id, name FROM test_data")
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][1] == "test"
        finally:
            await store.close()

    async def test_engine_selection_is_preserved_across_close_open(self, tmp_path):
        """Test that engine selection is preserved when store is closed and reopened."""
        # Create store with Postgres engine
        store = TestStorePostgres(tmp_path / "test.pg")
        assert store.engine == Engine.POSTGRES
        
        # Close it
        await store.close()
        
        # Engine should still be Postgres after close
        assert store.engine == Engine.POSTGRES
        
        # Note: We can't reopen with Postgres as it's not implemented yet
        # but the attribute should be preserved

    async def test_migration_chain_runs_for_sqlite(self, tmp_path):
        """Test that migrations still run when using SQLite."""
        store = TestStoreWithMigrations(tmp_path / "test.db")
        
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='migrated'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "migrated"
            
            cursor = await store._db.execute(
                "SELECT version FROM schema_migrations WHERE store_name = ?",
                ("TestStoreWithMigrations",),
            )
            versions = [row[0] for row in await cursor.fetchall()]
            assert 1 in versions
        finally:
            await store.close()
