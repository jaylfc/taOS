"""Lightweight versioned migration runner for taOS SQLite stores.

Design
------
Each store that uses this module registers an ordered list of migrations:

    MIGRATIONS = [
        (1, "CREATE TABLE IF NOT EXISTS foo (id INTEGER PRIMARY KEY)"),
        (2, "ALTER TABLE foo ADD COLUMN bar TEXT"),
    ]

On open, ``run_migrations(conn, MIGRATIONS, namespace="...")`` is called.  It:

1. Creates a ``schema_migrations`` table if absent.
2. Detects existing databases (tables present but no migration record) and
   **baselines** them at the highest version WITHOUT re-running any SQL.
   This keeps existing on-disk databases intact.
3. Applies any pending migrations in ascending version order.
4. Is idempotent: running it twice does nothing on the second pass.

Each migration is either a plain SQL string (executed via executescript) or a
callable ``fn(conn: sqlite3.Connection) -> None`` for cases that need Python
logic.

Namespacing
-----------
The ``store_name`` column (added retroactively) scopes migration versions to a
logical store so that two stores sharing the same DB file do not collide: store
A's v1 and store B's v1 are tracked independently.  On first run with a
namespace-aware caller, any pre-existing rows in ``schema_migrations`` that lack
a ``store_name`` are backfilled with ``'legacy'``.

Async variant
-------------
``run_migrations_async(conn, migrations, namespace="...")`` accepts an
``aiosqlite.Connection`` and awaits each step.  Use this in stores that open
their DB with aiosqlite.

WAL / synchronous helpers
--------------------------
``apply_wal_pragmas(conn)``        — sync sqlite3.Connection
``apply_wal_pragmas_async(conn)``  — async aiosqlite.Connection

Both set:
    PRAGMA busy_timeout = 5000
    PRAGMA journal_mode = WAL
    PRAGMA synchronous  = NORMAL

WAL gives better read concurrency and avoids most "database is locked" errors.
NORMAL synchronous is safe for nearly all crash scenarios while being
significantly faster than the default FULL.

FOOTGUNS -- READ BEFORE ADDING MIGRATIONS
------------------------------------------
1. SCHEMA runs before MIGRATIONS (see BaseStore.init).  Never put a
   reference to a column that is introduced by a migration inside SCHEMA
   (e.g. an index on a column added by ALTER TABLE).  The SCHEMA
   executescript will crash on existing databases that lack the column
   before the migration that adds it has had a chance to run.

2. Baseline-at-latest semantics (step 2 above) stamp pre-existing databases
   at the newest migration version WITHOUT executing any SQL.  This means
   retrofit migrations -- ones written for databases that predate them -- are
   silently skipped on exactly the databases that need them.  Use a guarded
   _post_init coroutine instead (PRAGMA table_info check + ALTER TABLE only
   when the column is absent).  See knowledge_store._migration_v1_add_user_id
   and agent_registry_store._migration_v1_add_status for the pattern.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable, Union

logger = logging.getLogger(__name__)

# A migration is a (version, sql_or_callable) pair.
Migration = tuple[int, Union[str, Callable[[sqlite3.Connection], None]]]

_TRACKING_SCHEMA = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    store_name  TEXT    NOT NULL,
    version     INTEGER NOT NULL,
    applied_at  REAL    NOT NULL,
    PRIMARY KEY (store_name, version)
);
"""

# ---------------------------------------------------------------------------
# Sync (sqlite3) API
# ---------------------------------------------------------------------------


def apply_wal_pragmas(conn: sqlite3.Connection) -> None:
    """Enable WAL journal mode and NORMAL synchronous on *conn*."""
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def _ensure_namespace_column(conn: sqlite3.Connection) -> None:
    """Add ``store_name`` column and rebuild PK on legacy ``schema_migrations``.

    Legacy databases have a ``schema_migrations`` table with
    ``PRIMARY KEY(version)``.  This function adds the missing ``store_name``
    column and rebuilds the table with ``PRIMARY KEY(store_name, version)``
    so that namespace isolation works correctly.
    """
    col_info = conn.execute("PRAGMA table_info(schema_migrations)").fetchall()
    col_names = [row[1] for row in col_info]

    if "store_name" not in col_names:
        try:
            conn.execute(
                "ALTER TABLE schema_migrations ADD COLUMN store_name TEXT NOT NULL DEFAULT 'legacy'"
            )
            conn.commit()
            logger.debug(
                "db_migrations: added store_name column to schema_migrations, "
                "backfilled existing rows with 'legacy'"
            )
        except sqlite3.OperationalError:
            pass
        # Re-fetch column info after ALTER TABLE.
        col_info = conn.execute("PRAGMA table_info(schema_migrations)").fetchall()

    # Rebuild with composite PK if still using legacy version-only PK.
    # Wrapped in explicit BEGIN/COMMIT so DROP→RENAME is atomic — a crash
    # mid-rebuild cannot leave the DB with no schema_migrations table.
    pk_cols = [row[1] for row in col_info if row[5]]
    if "store_name" not in pk_cols:
        conn.execute("BEGIN")
        try:
            conn.execute(
                "CREATE TABLE schema_migrations_new ("
                "    store_name  TEXT    NOT NULL,"
                "    version     INTEGER NOT NULL,"
                "    applied_at  REAL    NOT NULL,"
                "    PRIMARY KEY (store_name, version)"
                ")"
            )
            conn.execute(
                "INSERT INTO schema_migrations_new "
                "SELECT store_name, version, applied_at FROM schema_migrations"
            )
            conn.execute("DROP TABLE schema_migrations")
            conn.execute(
                "ALTER TABLE schema_migrations_new RENAME TO schema_migrations"
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        logger.debug(
            "db_migrations: rebuilt schema_migrations with composite PK "
            "(store_name, version)"
        )


def run_migrations(
    conn: sqlite3.Connection,
    migrations: list[Migration],
    *,
    namespace: str = "legacy",
) -> None:
    """Apply pending migrations to *conn* (sync sqlite3 version).

    Baselines existing databases so that on-disk DBs with tables but no
    migration record are stamped at the latest version instead of having
    all migrations re-run on them.

    *namespace* scopes migration tracking so two stores sharing the same DB
    file do not collide on version numbers.
    """
    import time

    # Create the tracking table.
    conn.executescript(_TRACKING_SCHEMA)
    conn.commit()

    # Backward compat: add store_name column if missing (pre-namespace DBs).
    _ensure_namespace_column(conn)

    if not migrations:
        return

    latest_version = max(v for v, _ in migrations)

    # Detect existing databases: any user table present means the DB was
    # created before this migration system existed.  Baseline without running.
    applied_row = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE store_name = ?",
        (namespace,),
    ).fetchone()[0]

    if applied_row == 0:
        # Check for pre-existing user tables (i.e. not the sqlite_* system
        # tables and not schema_migrations itself).
        existing_tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name != 'schema_migrations'"
        ).fetchone()[0]

        if existing_tables > 0:
            # Existing install — stamp at latest without running any SQL.
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (store_name, version, applied_at) VALUES (?, ?, ?)",
                (namespace, latest_version, time.time()),
            )
            conn.commit()
            logger.debug(
                "db_migrations: baselined existing DB at v%d for namespace %r (skipped %d migrations)",
                latest_version,
                namespace,
                len(migrations),
            )
            return

    # Collect applied versions for this namespace.
    applied = {
        row[0]
        for row in conn.execute(
            "SELECT version FROM schema_migrations WHERE store_name = ?",
            (namespace,),
        ).fetchall()
    }

    for version, step in sorted(migrations, key=lambda m: m[0]):
        if version in applied:
            continue
        logger.info("db_migrations: applying migration v%d for %r", version, namespace)
        if callable(step):
            step(conn)
        else:
            conn.executescript(step)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (store_name, version, applied_at) VALUES (?, ?, ?)",
            (namespace, version, time.time()),
        )
        conn.commit()

    logger.debug("db_migrations: schema up to date at v%d for %r", latest_version, namespace)


# ---------------------------------------------------------------------------
# Async (aiosqlite) API
# ---------------------------------------------------------------------------


async def apply_wal_pragmas_async(conn) -> None:
    """Enable WAL journal mode and NORMAL synchronous on an aiosqlite *conn*.

    busy_timeout is set explicitly (as in the sync variant) so a connection that
    meets a concurrent short write on the same file waits for it instead of
    failing the request with "database is locked".
    """
    await conn.execute("PRAGMA busy_timeout = 5000")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA synchronous = NORMAL")


async def _ensure_namespace_column_async(conn) -> None:
    """Add ``store_name`` column and rebuild PK on legacy ``schema_migrations``.

    Async variant of ``_ensure_namespace_column``.
    """
    col_info = await (await conn.execute("PRAGMA table_info(schema_migrations)")).fetchall()
    col_names = [row[1] for row in col_info]

    if "store_name" not in col_names:
        try:
            await conn.execute(
                "ALTER TABLE schema_migrations ADD COLUMN store_name TEXT NOT NULL DEFAULT 'legacy'"
            )
            await conn.commit()
            logger.debug(
                "db_migrations: added store_name column to schema_migrations, "
                "backfilled existing rows with 'legacy'"
            )
        except sqlite3.OperationalError:
            pass
        # Re-fetch column info after ALTER TABLE.
        col_info = await (await conn.execute("PRAGMA table_info(schema_migrations)")).fetchall()

    # Rebuild with composite PK if still using legacy version-only PK.
    # Wrapped in explicit BEGIN/COMMIT so DROP→RENAME is atomic — a crash
    # mid-rebuild cannot leave the DB with no schema_migrations table.
    pk_cols = [row[1] for row in col_info if row[5]]
    if "store_name" not in pk_cols:
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "CREATE TABLE schema_migrations_new ("
                "    store_name  TEXT    NOT NULL,"
                "    version     INTEGER NOT NULL,"
                "    applied_at  REAL    NOT NULL,"
                "    PRIMARY KEY (store_name, version)"
                ")"
            )
            await conn.execute(
                "INSERT INTO schema_migrations_new "
                "SELECT store_name, version, applied_at FROM schema_migrations"
            )
            await conn.execute("DROP TABLE schema_migrations")
            await conn.execute(
                "ALTER TABLE schema_migrations_new RENAME TO schema_migrations"
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        logger.debug(
            "db_migrations: rebuilt schema_migrations with composite PK "
            "(store_name, version)"
        )


async def run_migrations_async(
    conn,
    migrations: list[Migration],
    *,
    namespace: str = "legacy",
) -> None:
    """Apply pending migrations to *conn* (async aiosqlite version).

    Same semantics as ``run_migrations``; baselines existing databases.

    *namespace* scopes migration tracking so two stores sharing the same DB
    file do not collide on version numbers.
    """
    import time

    # Create the tracking table.
    await conn.executescript(_TRACKING_SCHEMA)
    await conn.commit()

    # Backward compat: add store_name column if missing (pre-namespace DBs).
    await _ensure_namespace_column_async(conn)

    if not migrations:
        return

    latest_version = max(v for v, _ in migrations)

    applied_row_count = (
        await (
            await conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE store_name = ?",
                (namespace,),
            )
        ).fetchone()
    )[0]

    if applied_row_count == 0:
        existing_tables = (
            await (
                await conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name != 'schema_migrations'"
                )
            ).fetchone()
        )[0]

        if existing_tables > 0:
            await conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (store_name, version, applied_at) VALUES (?, ?, ?)",
                (namespace, latest_version, time.time()),
            )
            await conn.commit()
            logger.debug(
                "db_migrations: baselined existing DB at v%d for namespace %r (skipped %d migrations)",
                latest_version,
                namespace,
                len(migrations),
            )
            return

    applied = {
        row[0]
        for row in await (
            await conn.execute(
                "SELECT version FROM schema_migrations WHERE store_name = ?",
                (namespace,),
            )
        ).fetchall()
    }

    for version, step in sorted(migrations, key=lambda m: m[0]):
        if version in applied:
            continue
        logger.info("db_migrations: applying migration v%d for %r", version, namespace)
        if callable(step):
            await step(conn)
        else:
            await conn.executescript(step)
        await conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (store_name, version, applied_at) VALUES (?, ?, ?)",
            (namespace, version, time.time()),
        )
        await conn.commit()

    logger.debug("db_migrations: schema up to date at v%d for %r", latest_version, namespace)
