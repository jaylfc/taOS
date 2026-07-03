from __future__ import annotations
import time
from tinyagentos.base_store import BaseStore


class InstalledAppsStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS installed_apps (
        app_id TEXT PRIMARY KEY,
        installed_at REAL NOT NULL,
        version TEXT DEFAULT '',
        metadata TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS app_runtime (
        app_id TEXT PRIMARY KEY,
        runtime_host TEXT NOT NULL,
        runtime_port INTEGER NOT NULL,
        backend TEXT DEFAULT '',
        ui_path TEXT DEFAULT '/'
    );
    """

    # App ids that were renamed. On boot, any install/runtime row keyed by the
    # old id is remapped to the new one so a user who installed the app under
    # its former name keeps it installed after the rename.
    _APP_ID_RENAMES = {"office-suite": "office-studio"}

    async def _post_init(self) -> None:
        assert self._db is not None
        for old_id, new_id in self._APP_ID_RENAMES.items():
            for table in ("installed_apps", "app_runtime"):
                # UPDATE OR IGNORE then DELETE: if a new-id row already exists
                # (the user somehow has both), the rename is ignored so the
                # newer row is preserved, and the stale old-id row is then
                # dropped. Never REPLACE, which would clobber the newer row.
                await self._db.execute(
                    f"UPDATE OR IGNORE {table} SET app_id = ? WHERE app_id = ?",
                    (new_id, old_id),
                )
                await self._db.execute(
                    f"DELETE FROM {table} WHERE app_id = ?",
                    (old_id,),
                )
        await self._db.commit()

    async def install(self, app_id: str, version: str = "", metadata: dict | None = None) -> None:
        import json
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO installed_apps (app_id, installed_at, version, metadata) VALUES (?, ?, ?, ?)",
            (app_id, time.time(), version, json.dumps(metadata or {})),
        )
        await self._db.commit()

    async def update_runtime_location(
        self,
        app_id: str,
        host: str,
        port: int,
        backend: str = "",
        ui_path: str = "/",
    ) -> None:
        """Record (or update) the current host:port where app_id is reachable."""
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO app_runtime (app_id, runtime_host, runtime_port, backend, ui_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (app_id, host, port, backend, ui_path),
        )
        await self._db.commit()

    async def get_runtime_location(self, app_id: str) -> dict | None:
        """Return the runtime location dict or None if not set."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT runtime_host, runtime_port, backend, ui_path FROM app_runtime WHERE app_id = ?",
            (app_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "runtime_host": row[0],
            "runtime_port": row[1],
            "backend": row[2],
            "ui_path": row[3],
        }

    async def uninstall(self, app_id: str) -> bool:
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM installed_apps WHERE app_id = ?",
            (app_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def is_installed(self, app_id: str) -> bool:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM installed_apps WHERE app_id = ?",
            (app_id,),
        )
        return await cursor.fetchone() is not None

    async def list_installed(self) -> list[dict]:
        import json
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT app_id, installed_at, version, metadata FROM installed_apps ORDER BY installed_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {"app_id": r[0], "installed_at": r[1], "version": r[2], "metadata": json.loads(r[3] or "{}")}
            for r in rows
        ]

    async def remove_runtime_location(self, app_id: str) -> None:
        """Remove the runtime location for app_id (called on uninstall)."""
        assert self._db is not None
        await self._db.execute("DELETE FROM app_runtime WHERE app_id = ?", (app_id,))
        await self._db.commit()
