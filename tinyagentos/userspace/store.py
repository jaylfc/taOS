from __future__ import annotations

import json
import time

from tinyagentos.base_store import BaseStore


class UserspaceAppStore(BaseStore):
    """Registry of installed userspace apps (sandboxed .taosapp packages).
    Distinct from InstalledAppsStore (catalog services). Userspace apps are
    web (iframe) or container; never in-process 'native'.

    BaseStore.init() runs SCHEMA and sets self._db — do NOT override init().
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS userspace_apps (
        app_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT '',
        app_type TEXT NOT NULL,
        entry TEXT NOT NULL DEFAULT 'index.html',
        icon TEXT NOT NULL DEFAULT '',
        permissions_requested TEXT NOT NULL DEFAULT '[]',
        permissions_granted TEXT NOT NULL DEFAULT '[]',
        enabled INTEGER NOT NULL DEFAULT 1,
        installed_at INTEGER NOT NULL,
        container_host TEXT,
        container_port INTEGER
    );
    """

    async def install(self, app_id, name, version, app_type, entry, icon,
                      permissions_requested):
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO userspace_apps
               (app_id, name, version, app_type, entry, icon,
                permissions_requested, permissions_granted, enabled, installed_at)
               VALUES (?,?,?,?,?,?,?,'[]',1,?)
               ON CONFLICT(app_id) DO UPDATE SET
                 name=excluded.name, version=excluded.version,
                 app_type=excluded.app_type, entry=excluded.entry,
                 icon=excluded.icon,
                 permissions_requested=excluded.permissions_requested""",
            (app_id, name, version, app_type, entry, icon,
             json.dumps(permissions_requested), int(time.time())),
        )
        await self._db.commit()

    def _row_to_dict(self, row) -> dict:
        return {
            "app_id": row[0], "name": row[1], "version": row[2],
            "app_type": row[3], "entry": row[4], "icon": row[5],
            "permissions_requested": json.loads(row[6]),
            "permissions_granted": json.loads(row[7]),
            "enabled": row[8], "installed_at": row[9],
            "container_host": row[10], "container_port": row[11],
        }

    async def get(self, app_id) -> dict | None:
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM userspace_apps WHERE app_id=?", (app_id,))
        row = await cur.fetchone()
        return self._row_to_dict(row) if row else None

    async def list_installed(self) -> list[dict]:
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM userspace_apps ORDER BY installed_at")
        return [self._row_to_dict(r) for r in await cur.fetchall()]

    async def set_permissions_granted(self, app_id, perms):
        assert self._db is not None
        await self._db.execute("UPDATE userspace_apps SET permissions_granted=? WHERE app_id=?",
                               (json.dumps(perms), app_id))
        await self._db.commit()

    async def set_enabled(self, app_id, enabled: bool):
        assert self._db is not None
        await self._db.execute("UPDATE userspace_apps SET enabled=? WHERE app_id=?",
                               (1 if enabled else 0, app_id))
        await self._db.commit()

    async def set_runtime_location(self, app_id, host: str, port: int):
        assert self._db is not None
        await self._db.execute(
            "UPDATE userspace_apps SET container_host=?, container_port=? WHERE app_id=?",
            (host, port, app_id),
        )
        await self._db.commit()

    async def uninstall(self, app_id) -> bool:
        assert self._db is not None
        cur = await self._db.execute("DELETE FROM userspace_apps WHERE app_id=?", (app_id,))
        await self._db.commit()
        return cur.rowcount > 0
