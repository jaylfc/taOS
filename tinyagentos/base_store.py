from __future__ import annotations
from pathlib import Path
import aiosqlite

from tinyagentos.db_migrations import apply_wal_pragmas_async, run_migrations_async


class BaseStore:
    """Base class for all SQLite-backed stores.

    Subclasses set ``SCHEMA`` (applied once on first open) and may set
    ``MIGRATIONS`` to a list of ``(version, sql_or_callable)`` pairs that
    will be tracked and applied in order by the migration runner.
    """
    SCHEMA: str = ""
    # List of (version: int, sql_or_callable) pairs. See db_migrations.py.
    MIGRATIONS: list = []

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await apply_wal_pragmas_async(self._db)
        if self.SCHEMA:
            await self._db.executescript(self.SCHEMA)
            await self._db.commit()
        if self.MIGRATIONS:
            await run_migrations_async(self._db, self.MIGRATIONS)
        await self._post_init()

    async def _post_init(self) -> None:
        """Override in subclasses for seeding data after schema creation."""
        pass

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
