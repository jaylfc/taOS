"""Tests for BaseStore -- init, schema, connection, close, migrations."""
import aiosqlite
import pytest
import sqlite3

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
                "SELECT version FROM schema_migrations WHERE store_name = ?",
                ("TestStore",),
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
                "SELECT version FROM schema_migrations WHERE store_name = ? ORDER BY version",
                ("TestStore",),
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
                "SELECT version FROM schema_migrations WHERE store_name = ?",
                ("TestStoreEmptySchema",),
            )
            versions = [row[0] for row in await cursor.fetchall()]
            assert 1 in versions
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# CRUD coverage: BaseStore exposes no CRUD itself, so a concrete subclass with a
# real schema is exercised end-to-end against a tmp_path database.  This pins
# the BaseStore plumbing (init -> connection -> commit/rollback -> close) that
# every CRUD subclass depends on, plus the per-row / owner-scoping semantics
# those subclasses build on top of it.
# ---------------------------------------------------------------------------

_COLUMNS = ["id", "owner", "key", "value"]


def _row_to_dict(row) -> dict | None:
    return dict(zip(_COLUMNS, row)) if row is not None else None


class CrudStore(BaseStore):
    """Minimal concrete store exercising BaseStore's connection lifecycle."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS items (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT,
        key   TEXT NOT NULL,
        value TEXT,
        UNIQUE(owner, key)
    );
    """

    async def create_item(self, owner: str | None, key: str, value: str) -> int:
        cursor = await self._db.execute(
            "INSERT INTO items (owner, key, value) VALUES (?, ?, ?)",
            (owner, key, value),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_item(self, item_id: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT id, owner, key, value FROM items WHERE id = ?", (item_id,)
        )
        return _row_to_dict(await cursor.fetchone())

    async def list_items(self, owner: str | None = None) -> list[dict]:
        if owner is None:
            cursor = await self._db.execute(
                "SELECT id, owner, key, value FROM items ORDER BY id"
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, owner, key, value FROM items WHERE owner = ? ORDER BY id",
                (owner,),
            )
        return [_row_to_dict(row) for row in await cursor.fetchall()]

    async def update_item(self, item_id: int, value: str) -> int:
        cursor = await self._db.execute(
            "UPDATE items SET value = ? WHERE id = ?", (value, item_id)
        )
        await self._db.commit()
        return cursor.rowcount

    async def delete_item(self, item_id: int) -> int:
        cursor = await self._db.execute(
            "DELETE FROM items WHERE id = ?", (item_id,)
        )
        await self._db.commit()
        return cursor.rowcount


@pytest.mark.asyncio
class TestCrudStore:

    async def _make_store(self, tmp_path) -> CrudStore:
        store = CrudStore(tmp_path / "crud.db")
        await store.init()
        return store

    async def test_create_returns_id_and_persists_row(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            item_id = await store.create_item("alice", "k1", "v1")
            assert item_id == 1
            row = await store.get_item(item_id)
            assert row == {"id": 1, "owner": "alice", "key": "k1", "value": "v1"}
        finally:
            await store.close()

    async def test_create_assigns_increasing_ids(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            first = await store.create_item("alice", "k1", "v1")
            second = await store.create_item("bob", "k2", "v2")
            assert second == first + 1
        finally:
            await store.close()

    async def test_read_missing_id_returns_none(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            assert await store.get_item(999) is None
        finally:
            await store.close()

    async def test_empty_list_returns_empty(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            assert await store.list_items() == []
            assert await store.list_items("alice") == []
        finally:
            await store.close()

    async def test_update_modifies_value_and_returns_rowcount(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            item_id = await store.create_item("alice", "k1", "v1")
            affected = await store.update_item(item_id, "v2")
            assert affected == 1
            row = await store.get_item(item_id)
            assert row["value"] == "v2"
            assert row["key"] == "k1"
        finally:
            await store.close()

    async def test_update_missing_id_affects_zero_rows(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            affected = await store.update_item(999, "v")
            assert affected == 0
            assert await store.get_item(999) is None
        finally:
            await store.close()

    async def test_delete_removes_row_and_returns_rowcount(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            item_id = await store.create_item("alice", "k1", "v1")
            affected = await store.delete_item(item_id)
            assert affected == 1
            assert await store.get_item(item_id) is None
            assert await store.list_items() == []
        finally:
            await store.close()

    async def test_delete_missing_id_affects_zero_rows(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            affected = await store.delete_item(999)
            assert affected == 0
            assert await store.get_item(999) is None
        finally:
            await store.close()

    async def test_duplicate_owner_key_raises_integrity(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            await store.create_item("alice", "k1", "v1")
            with pytest.raises(sqlite3.IntegrityError):
                await store.create_item("alice", "k1", "v2")
            # The failed insert must leave no partial row; the original survives.
            await store._db.rollback()
            rows = await store.list_items()
            assert len(rows) == 1
            assert rows[0]["value"] == "v1"
        finally:
            await store.close()

    async def test_duplicate_key_allowed_across_owners(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            alice_id = await store.create_item("alice", "k1", "v1")
            bob_id = await store.create_item("bob", "k1", "v2")
            assert bob_id != alice_id
            rows = await store.list_items()
            assert len(rows) == 2
            values_by_owner = {r["owner"]: r["value"] for r in rows}
            assert values_by_owner == {"alice": "v1", "bob": "v2"}
        finally:
            await store.close()

    async def test_scoping_by_owner_isolates_rows(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            await store.create_item("alice", "a1", "1")
            await store.create_item("alice", "a2", "2")
            await store.create_item("bob", "b1", "3")
            alice = await store.list_items("alice")
            assert [r["key"] for r in alice] == ["a1", "a2"]
            bob = await store.list_items("bob")
            assert [r["key"] for r in bob] == ["b1"]
            all_rows = await store.list_items()
            assert len(all_rows) == 3
        finally:
            await store.close()

    async def test_delete_is_scoped_by_id_not_owner(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            alice_id = await store.create_item("alice", "a1", "1")
            bob_id = await store.create_item("bob", "b1", "3")
            affected = await store.delete_item(alice_id)
            assert affected == 1
            assert await store.get_item(alice_id) is None
            # Bob's row must be untouched by alice's delete.
            bob_row = await store.get_item(bob_id)
            assert bob_row is not None
            assert bob_row["owner"] == "bob"
            assert len(await store.list_items()) == 1
        finally:
            await store.close()

    async def test_update_is_scoped_by_id_not_owner(self, tmp_path):
        store = await self._make_store(tmp_path)
        try:
            alice_id = await store.create_item("alice", "a1", "1")
            bob_id = await store.create_item("bob", "b1", "3")
            affected = await store.update_item(alice_id, "updated")
            assert affected == 1
            assert (await store.get_item(bob_id))["value"] == "3"
            assert (await store.get_item(alice_id))["value"] == "updated"
        finally:
            await store.close()
