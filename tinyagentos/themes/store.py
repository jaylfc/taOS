from __future__ import annotations
import json, time
from tinyagentos.base_store import BaseStore

class ThemeStore(BaseStore):
    """Registry of installed themes. BaseStore.init() runs SCHEMA + sets self._db."""
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS themes (
        theme_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT '',
        config TEXT NOT NULL DEFAULT '{}',
        installed_at INTEGER NOT NULL
    );
    """

    async def install(self, theme_id, name, version, config):
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO themes (theme_id, name, version, config, installed_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(theme_id) DO UPDATE SET
                 name=excluded.name, version=excluded.version, config=excluded.config""",
            (theme_id, name, version, json.dumps(config), int(time.time())),
        )
        await self._db.commit()

    def _row(self, r):
        return {"theme_id": r[0], "name": r[1], "version": r[2],
                "config": json.loads(r[3]), "installed_at": r[4]}

    async def get(self, theme_id):
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM themes WHERE theme_id=?", (theme_id,))
        row = await cur.fetchone()
        return self._row(row) if row else None

    async def list_installed(self):
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM themes ORDER BY installed_at")
        return [self._row(r) for r in await cur.fetchall()]

    async def remove(self, theme_id) -> bool:
        assert self._db is not None
        cur = await self._db.execute("DELETE FROM themes WHERE theme_id=?", (theme_id,))
        await self._db.commit()
        return cur.rowcount > 0
