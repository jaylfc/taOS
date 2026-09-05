from __future__ import annotations
from pathlib import Path
from enum import Enum


class Engine(Enum):
    """Database engine selection."""
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class PendingCapExceeded(Exception):
    """A store refused an insert because a per-key pending cap is already full.

    Raised by a ``create()`` that enforces the cap INSIDE its INSERT statement
    rather than leaving the caller to count first.  A count-then-insert cap is
    bypassable by exactly the traffic caps exist to stop: every request in a
    concurrent burst reads the same pre-insert count, every one of them passes
    the check, and every one of them inserts.

    ``pending`` is read back after the refusal, so it is a description for the
    error message, not the value the decision was made on -- that comparison
    happened atomically in SQL.  Routes map this to 429.
    """

    def __init__(self, *, key: str, cap: int, pending: int):
        self.key = key
        self.cap = cap
        self.pending = pending
        super().__init__(f"{pending} pending at cap {cap} for {key!r}")


class BaseStore:
    """Base class for all SQLite-backed stores.

    Subclasses set ``SCHEMA`` (applied once on first open) and may set
    ``MIGRATIONS`` to a list of ``(version, sql_or_callable)`` pairs that
    will be tracked and applied in order by the migration runner.

    WARNING: init order is SCHEMA -> MIGRATIONS -> _post_init.  Never
    reference a column inside SCHEMA that is introduced by a migration —
    the SCHEMA executescript runs first and will crash on any existing
    database that lacks the column.  This applies to CREATE INDEX too:
    any CREATE INDEX in SCHEMA that references a column added by _post_init
    will brick boot on existing databases — the column won't exist yet when
    the index is created.  The fix is to move such indexes into _post_init
    after the ALTER TABLE that introduces the column.

    The migration runner uses baseline-at-latest semantics: pre-existing
    databases are stamped at the latest version without executing any SQL,
    so retrofit migrations are silently skipped.  Use a guarded _post_init
    coroutine (PRAGMA table_info check + ALTER TABLE only when absent) for
    columns added after initial release.  See db_migrations.py module
    docstring and tests/test_store_upgrades.py for the regression gate.
    """
    SCHEMA: str = ""
    # List of (version: int, sql_or_callable) pairs. See db_migrations.py.
    MIGRATIONS: list = []
    # Database engine: SQLITE (default) or POSTGRES
    ENGINE: Engine = Engine.SQLITE
    # Open the sqlite connection in autocommit mode (isolation_level=None), so
    # the driver never opens an implicit transaction that an error path could
    # leave dangling on the connection.  A store that sets this MUST wrap every
    # multi-statement write in an explicit transaction -- see
    # tinyagentos/projects/tx.py, which does exactly that for the eight stores
    # sharing projects.db.
    AUTOCOMMIT: bool = False

    def __init__(self, db_path: Path, engine: Engine | None = None):
        self.db_path = db_path
        self.engine = engine if engine is not None else self.ENGINE
        self._db = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if self.engine == Engine.POSTGRES:
            await self._init_postgres()
        else:
            await self._init_sqlite()

    async def _init_sqlite(self) -> None:
        import aiosqlite
        from tinyagentos.db_migrations import apply_wal_pragmas_async, run_migrations_async

        connect_kwargs = {"isolation_level": None} if self.AUTOCOMMIT else {}
        self._db = await aiosqlite.connect(str(self.db_path), **connect_kwargs)
        await apply_wal_pragmas_async(self._db)
        if self.SCHEMA:
            await self._db.executescript(self.SCHEMA)
            await self._db.commit()
        if self.MIGRATIONS:
            await run_migrations_async(self._db, self.MIGRATIONS,
                                       namespace=self.__class__.__name__)
        await self._post_init()

    async def _init_postgres(self) -> None:
        raise NotImplementedError("Postgres engine not yet implemented")

    async def _post_init(self) -> None:
        """Override in subclasses for seeding data after schema creation."""
        pass

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
