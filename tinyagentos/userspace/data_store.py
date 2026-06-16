from __future__ import annotations

import json

from tinyagentos.base_store import BaseStore


class UserspaceDataStore(BaseStore):
    """Per-app KV + table storage, namespaced by app_id. Every read/write is
    filtered by app_id so one app can never see another app's data."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS app_kv (
        app_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        PRIMARY KEY (app_id, key)
    );
    CREATE TABLE IF NOT EXISTS app_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT NOT NULL,
        table_name TEXT NOT NULL,
        row_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_app_rows ON app_rows (app_id, table_name);
    """

    async def kv_get(self, app_id: str, key: str):
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT value_json FROM app_kv WHERE app_id=? AND key=?", (app_id, key))
        row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def kv_set(self, app_id: str, key: str, value) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO app_kv (app_id, key, value_json) VALUES (?,?,?) "
            "ON CONFLICT(app_id, key) DO UPDATE SET value_json=excluded.value_json",
            (app_id, key, json.dumps(value)))
        await self._db.commit()

    async def kv_delete(self, app_id: str, key: str) -> None:
        assert self._db is not None
        await self._db.execute("DELETE FROM app_kv WHERE app_id=? AND key=?", (app_id, key))
        await self._db.commit()

    async def kv_keys(self, app_id: str) -> list[str]:
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT key FROM app_kv WHERE app_id=? ORDER BY key", (app_id,))
        return [r[0] for r in await cur.fetchall()]

    async def table_insert(self, app_id: str, table: str, row: dict) -> int:
        assert self._db is not None
        cur = await self._db.execute(
            "INSERT INTO app_rows (app_id, table_name, row_json) VALUES (?,?,?)",
            (app_id, table, json.dumps(row)))
        await self._db.commit()
        return cur.lastrowid

    async def table_query(self, app_id: str, table: str, where: dict | None) -> list[dict]:
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT id, row_json FROM app_rows WHERE app_id=? AND table_name=? ORDER BY id",
            (app_id, table))
        out = []
        for rid, row_json in await cur.fetchall():
            data = json.loads(row_json)
            if where and any(data.get(k) != v for k, v in where.items()):
                continue
            out.append({"id": rid, **data})
        return out

    async def table_delete(self, app_id: str, table: str, row_id: int) -> None:
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM app_rows WHERE app_id=? AND table_name=? AND id=?",
            (app_id, table, row_id))
        await self._db.commit()
