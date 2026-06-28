"""Tests for BaseStore — init, schema, connection, close, migrations."""
import aiosqlite
import pytest

from tinyagentos.base_store import BaseStore


class TestStore(BaseStore):
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS items (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
    """
    MIGRATIONS = [
        (1, "CREATE TABLE IF NOT EXISTS extras (id INTEGER PRIMARY KEY, info TEXT)"),
    ]


class TestStoreEmptySchema(BaseStore):
    SCHEMA = ""
    MIGRATIONS = [
        (1, "CREATE TABLE IF NOT EXISTS migrated (id INTEGER PRIMARY KEY, data TEXT)"),
    ]


class TestStoreNoMigration(BaseStore):
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS things (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            val  TEXT NOT NULL
        );
    """


@pytest.mark.asyncio
class TestBaseStore:
    async def _make_store(self, db_path):
        store = TestStore(db_path)
        await store.init()
        return store

    async def test_init_creates_db_and_schema(self, tmp_path):
        db_path = tmp_path / "test.db"
        store = await self._make_store(db_path)
        try:
            assert db_path.exists()
            assert store._db is not None
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "items"
        finally:
            await store.close()

    async def test_insert_and_select_round_trip(self, tmp_path):
        store = await self._make_store(tmp_path / "test.db")
        try:
            await store._db.execute("INSERT INTO items (name) VALUES (?)", ("hello",))
            await store._db.commit()
            cursor = await store._db.execute("SELECT id, name FROM items")
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][1] == "hello"
            assert rows[0][0] == 1
        finally:
            await store.close()

    async def test_close_closes_connection(self, tmp_path):
        store = await self._make_store(tmp_path / "test.db")
        await store.close()
        assert store._db is None

    async def test_re_init_on_fresh_tmp_path(self, tmp_path):
        db_path = tmp_path / "reinit.db"
        store = TestStore(db_path)
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
            )
            row = await cursor.fetchone()
            assert row is not None
        finally:
            await store.close()

    async def test_migrations_recorded_after_init(self, tmp_path):
        db_path = tmp_path / "migrate.db"
        store = await self._make_store(db_path)
        try:
            cursor = await store._db.execute(
                "SELECT version FROM schema_migrations"
            )
            versions = [row[0] for row in await cursor.fetchall()]
            assert 1 in versions
        finally:
            await store.close()

    async def test_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "idempotent.db"
        store = TestStore(db_path)
        await store.init()
        await store.close()

        store2 = TestStore(db_path)
        await store2.init()
        try:
            cursor = await store2._db.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
            versions = [row[0] for row in await cursor.fetchall()]
            assert versions == [1]
        finally:
            await store2.close()

    async def test_no_migration_store_skips_migration_runner(self, tmp_path):
        db_path = tmp_path / "no_migrate.db"
        store = TestStoreNoMigration(db_path)
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='things'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "things"
        finally:
            await store.close()

    async def test_migration_runs_on_empty_schema(self, tmp_path):
        db_path = tmp_path / "empty_schema.db"
        store = TestStoreEmptySchema(db_path)
        await store.init()
        try:
            cursor = await store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='migrated'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "migrated"

            cursor = await store._db.execute(
                "SELECT version FROM schema_migrations"
            )
            versions = [row[0] for row in await cursor.fetchall()]
            assert 1 in versions
        finally:
            await store.close()
