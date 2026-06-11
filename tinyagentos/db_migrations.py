"""Lightweight versioned migration runner for taOS SQLite stores.

Design
------
Each store that uses this module registers an ordered list of migrations:

    MIGRATIONS = [
        (1, "CREATE TABLE IF NOT EXISTS foo (id INTEGER PRIMARY KEY)"),
        (2, "ALTER TABLE foo ADD COLUMN bar TEXT"),
    ]

On open, ``run_migrations(conn, MIGRATIONS)`` is called.  It:

1. Creates a ``schema_migrations`` table if absent.
2. Detects existing databases (tables present but no migration record) and
   **baselines** them at the highest version WITHOUT re-running any SQL.
   This keeps existing on-disk databases intact.
3. Applies any pending migrations in ascending version order.
4. Is idempotent: running it twice does nothing on the second pass.

Each migration is either a plain SQL string (executed via executescript) or a
callable ``fn(conn: sqlite3.Connection) -> None`` for cases that need Python
logic.

Async variant
-------------
``run_migrations_async(conn, migrations)`` accepts an ``aiosqlite.Connection``
and awaits each step.  Use this in stores that open their DB with aiosqlite.

WAL / synchronous helpers
--------------------------
``apply_wal_pragmas(conn)``        — sync sqlite3.Connection
``apply_wal_pragmas_async(conn)``  — async aiosqlite.Connection

Both set:
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

_TRACKING_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  REAL    NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Sync (sqlite3) API
# ---------------------------------------------------------------------------

def apply_wal_pragmas(conn: sqlite3.Connection) -> None:
    """Enable WAL journal mode and NORMAL synchronous on *conn*."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def run_migrations(
    conn: sqlite3.Connection,
    migrations: list[Migration],
) -> None:
    """Apply pending migrations to *conn* (sync sqlite3 version).

    Baselines existing databases so that on-disk DBs with tables but no
    migration record are stamped at the latest version instead of having
    all migrations re-run on them.
    """
    import time

    # Create the tracking table.
    conn.executescript(_TRACKING_SCHEMA)
    conn.commit()

    if not migrations:
        return

    latest_version = max(v for v, _ in migrations)

    # Detect existing databases: any user table present means the DB was
    # created before this migration system existed.  Baseline without running.
    applied_row = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations"
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
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (latest_version, time.time()),
            )
            conn.commit()
            logger.debug(
                "db_migrations: baselined existing DB at v%d (skipped %d migrations)",
                latest_version,
                len(migrations),
            )
            return

    # Collect applied versions.
    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    for version, step in sorted(migrations, key=lambda m: m[0]):
        if version in applied:
            continue
        logger.info("db_migrations: applying migration v%d", version)
        if callable(step):
            step(conn)
        else:
            conn.executescript(step)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, time.time()),
        )
        conn.commit()

    logger.debug("db_migrations: schema up to date at v%d", latest_version)


# ---------------------------------------------------------------------------
# Async (aiosqlite) API
# ---------------------------------------------------------------------------

async def apply_wal_pragmas_async(conn) -> None:
    """Enable WAL journal mode and NORMAL synchronous on an aiosqlite *conn*."""
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA synchronous = NORMAL")


async def run_migrations_async(
    conn,
    migrations: list[Migration],
) -> None:
    """Apply pending migrations to *conn* (async aiosqlite version).

    Same semantics as ``run_migrations``; baselines existing databases.
    """
    import time

    # Create the tracking table.
    await conn.executescript(_TRACKING_SCHEMA)
    await conn.commit()

    if not migrations:
        return

    latest_version = max(v for v, _ in migrations)

    applied_row_count = (
        await (await conn.execute("SELECT COUNT(*) FROM schema_migrations")).fetchone()
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
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (latest_version, time.time()),
            )
            await conn.commit()
            logger.debug(
                "db_migrations: baselined existing DB at v%d (skipped %d migrations)",
                latest_version,
                len(migrations),
            )
            return

    applied = {
        row[0]
        for row in await (
            await conn.execute("SELECT version FROM schema_migrations")
        ).fetchall()
    }

    for version, step in sorted(migrations, key=lambda m: m[0]):
        if version in applied:
            continue
        logger.info("db_migrations: applying migration v%d", version)
        if callable(step):
            await step(conn)
        else:
            await conn.executescript(step)
        await conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, time.time()),
        )
        await conn.commit()

    logger.debug("db_migrations: schema up to date at v%d", latest_version)
