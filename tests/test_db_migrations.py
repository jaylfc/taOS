"""Tests for the lightweight db_migrations module.

Covers:
  - Fresh DB: migrations applied in order, tracking table populated.
  - Existing DB: baselines at latest version without re-running SQL.
  - Idempotent re-run: running again does nothing.
  - Callable migration: Python function accepted alongside SQL strings.
  - WAL pragma helpers: journal_mode and synchronous set correctly.
  - Async variants: run_migrations_async / apply_wal_pragmas_async.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.db_migrations import (
    apply_wal_pragmas,
    apply_wal_pragmas_async,
    run_migrations,
    run_migrations_async,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _applied_versions(conn: sqlite3.Connection, namespace: str = "legacy") -> list[int]:
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations WHERE store_name = ? ORDER BY version",
            (namespace,),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


V1_SQL = "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
V2_SQL = "ALTER TABLE items ADD COLUMN tag TEXT;"


# ---------------------------------------------------------------------------
# Sync API
# ---------------------------------------------------------------------------

class TestRunMigrationsSync:
    def test_fresh_db_applies_all(self, tmp_path):
        conn = _open(tmp_path / "fresh.db")
        migrations = [(1, V1_SQL), (2, V2_SQL)]
        run_migrations(conn, migrations)

        assert _applied_versions(conn) == [1, 2]
        # Both columns should exist
        conn.execute("INSERT INTO items (id, name, tag) VALUES (1, 'a', 'x')")
        row = conn.execute("SELECT tag FROM items WHERE id = 1").fetchone()
        assert row[0] == "x"
        conn.close()

    def test_existing_db_baselines_without_rerun(self, tmp_path):
        db_path = tmp_path / "existing.db"
        # Simulate a DB created before the migration system: tables exist, no
        # schema_migrations table.
        conn = _open(db_path)
        conn.executescript(V1_SQL)
        conn.executescript(V2_SQL)
        conn.commit()
        conn.close()

        # Now run migrations — should baseline, NOT re-run SQL.
        conn = _open(db_path)
        migrations = [(1, V1_SQL), (2, V2_SQL)]
        run_migrations(conn, migrations)

        # Should be baselined at v2.
        assert _applied_versions(conn) == [2]
        conn.close()

    def test_idempotent_rerun(self, tmp_path):
        conn = _open(tmp_path / "idem.db")
        migrations = [(1, V1_SQL)]
        run_migrations(conn, migrations)
        # Run again — should not raise or duplicate records.
        run_migrations(conn, migrations)
        assert _applied_versions(conn) == [1]
        conn.close()

    def test_partial_apply(self, tmp_path):
        """Apply v1 first, then add v2 in a second call."""
        conn = _open(tmp_path / "partial.db")
        run_migrations(conn, [(1, V1_SQL)])
        assert _applied_versions(conn) == [1]

        run_migrations(conn, [(1, V1_SQL), (2, V2_SQL)])
        assert _applied_versions(conn) == [1, 2]
        conn.close()

    def test_callable_migration(self, tmp_path):
        """A callable is accepted instead of a SQL string."""
        conn = _open(tmp_path / "callable.db")

        def _create_table(c: sqlite3.Connection) -> None:
            c.execute(
                "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY)"
            )
            c.commit()

        run_migrations(conn, [(1, _create_table)])
        assert _applied_versions(conn) == [1]
        # Table should exist.
        conn.execute("INSERT INTO widgets (id) VALUES (42)")
        assert conn.execute("SELECT id FROM widgets").fetchone()[0] == 42
        conn.close()

    def test_empty_migrations_list(self, tmp_path):
        conn = _open(tmp_path / "empty.db")
        run_migrations(conn, [])
        # Tracking table still created.
        assert _applied_versions(conn) == []
        conn.close()

    def test_namespace_isolation(self, tmp_path):
        """Two namespaces can share a DB file without version collisions."""
        conn = _open(tmp_path / "ns.db")
        run_migrations(conn, [(1, V1_SQL)], namespace="StoreA")
        run_migrations(conn, [(1, V1_SQL)], namespace="StoreB")
        assert _applied_versions(conn, namespace="StoreA") == [1]
        assert _applied_versions(conn, namespace="StoreB") == [1]
        conn.close()

    def test_namespace_backfill_legacy(self, tmp_path):
        """Pre-namespace DB rows are backfilled with 'legacy' and PK rebuilt."""
        db_path = tmp_path / "legacy.db"
        # Create a pre-namespace schema_migrations table with
        # the table needed by V2_SQL already in place (mimicking a real
        # legacy DB that already went through v1).
        conn = _open(db_path)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "    version INTEGER PRIMARY KEY,"
            "    applied_at REAL NOT NULL"
            ");"
        )
        conn.executescript(V1_SQL)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (1, 0.0)"
        )
        conn.commit()
        conn.close()

        # Now run with the new migration runner — should backfill, rebuild
        # the PK to composite, and apply the pending v2 migration.
        conn = _open(db_path)
        run_migrations(conn, [(2, V2_SQL)], namespace="legacy")
        assert _applied_versions(conn, namespace="legacy") == [1, 2]

        # PK should now be composite (store_name, version).
        pk_cols = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(schema_migrations)"
            ).fetchall()
            if row[5]
        ]
        assert pk_cols == ["store_name", "version"]

        # A second namespace can record the same version number.
        run_migrations(conn, [(1, V1_SQL)], namespace="OtherStore")
        assert _applied_versions(conn, namespace="OtherStore") == [1]
        conn.close()


class TestApplyWalPragmasSync:
    def test_wal_mode_set(self, tmp_path):
        conn = _open(tmp_path / "wal.db")
        apply_wal_pragmas(conn)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_synchronous_normal(self, tmp_path):
        conn = _open(tmp_path / "sync.db")
        apply_wal_pragmas(conn)
        # PRAGMA synchronous returns 1 for NORMAL.
        val = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert val == 1
        conn.close()


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRunMigrationsAsync:
    async def test_fresh_db_applies_all(self, tmp_path):
        import aiosqlite
        db_path = str(tmp_path / "async_fresh.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await run_migrations_async(conn, [(1, V1_SQL), (2, V2_SQL)])

        cur = await conn.execute(
            "SELECT version FROM schema_migrations WHERE store_name = 'legacy' ORDER BY version"
        )
        versions = [r[0] for r in await cur.fetchall()]
        assert versions == [1, 2]
        await conn.close()

    async def test_existing_db_baselines(self, tmp_path):
        import aiosqlite
        db_path = str(tmp_path / "async_existing.db")
        # Create tables without migration tracking.
        conn = await aiosqlite.connect(db_path)
        await conn.executescript(V1_SQL)
        await conn.commit()
        await conn.close()

        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await run_migrations_async(conn, [(1, V1_SQL)])

        cur = await conn.execute(
            "SELECT version FROM schema_migrations WHERE store_name = 'legacy' ORDER BY version"
        )
        versions = [r[0] for r in await cur.fetchall()]
        assert versions == [1]
        await conn.close()

    async def test_idempotent(self, tmp_path):
        import aiosqlite
        conn = await aiosqlite.connect(str(tmp_path / "async_idem.db"))
        await run_migrations_async(conn, [(1, V1_SQL)])
        await run_migrations_async(conn, [(1, V1_SQL)])
        cur = await conn.execute(
            "SELECT version FROM schema_migrations WHERE store_name = 'legacy' ORDER BY version"
        )
        versions = [r[0] for r in await cur.fetchall()]
        assert versions == [1]
        await conn.close()


@pytest.mark.asyncio
class TestApplyWalPragmasAsync:
    async def test_wal_mode_set(self, tmp_path):
        import aiosqlite
        conn = await aiosqlite.connect(str(tmp_path / "async_wal.db"))
        await apply_wal_pragmas_async(conn)
        cur = await conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
        assert row[0] == "wal"
        await conn.close()

    async def test_synchronous_normal(self, tmp_path):
        import aiosqlite
        conn = await aiosqlite.connect(str(tmp_path / "async_sync.db"))
        await apply_wal_pragmas_async(conn)
        cur = await conn.execute("PRAGMA synchronous")
        row = await cur.fetchone()
        assert row[0] == 1
        await conn.close()

    async def test_busy_timeout_set(self, tmp_path):
        # Parity with the sync helper: a connection that meets a concurrent
        # short write on the same file must wait instead of failing the
        # request with "database is locked".
        import aiosqlite
        conn = await aiosqlite.connect(str(tmp_path / "async_busy.db"))
        await apply_wal_pragmas_async(conn)
        cur = await conn.execute("PRAGMA busy_timeout")
        row = await cur.fetchone()
        assert row[0] == 5000
        await conn.close()
