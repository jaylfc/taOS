from __future__ import annotations

import secrets
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

INSTALL_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS install_registry (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    version TEXT NOT NULL,
    location_kind TEXT NOT NULL,
    location_ref TEXT NOT NULL,
    update_channel TEXT NOT NULL DEFAULT 'stable',
    installed_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(item_id, location_ref)
);
"""


def _new_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"ir-{suffix}"


class InstallRegistryStore(BaseStore):
    SCHEMA = INSTALL_REGISTRY_SCHEMA

    async def record(
        self,
        item_id: str,
        item_kind: str,
        version: str,
        location_kind: str,
        location_ref: str,
        update_channel: str = "stable",
    ) -> dict:
        now = int(time.time())
        async with self._db.execute(
            "SELECT id FROM install_registry WHERE item_id = ? AND location_ref = ?",
            (item_id, location_ref),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            await self._db.execute(
                """UPDATE install_registry
                   SET item_kind = ?, version = ?, location_kind = ?,
                       update_channel = ?, updated_at = ?
                   WHERE id = ?""",
                (item_kind, version, location_kind, update_channel, now, existing[0]),
            )
            await self._db.commit()
            return await self.get(existing[0])
        for _ in range(8):
            iid = _new_id()
            async with self._db.execute(
                "SELECT 1 FROM install_registry WHERE id = ?", (iid,)
            ) as cur:
                if await cur.fetchone() is None:
                    break
        else:
            raise RuntimeError("could not allocate install registry id")
        row = {
            "id": iid,
            "item_id": item_id,
            "item_kind": item_kind,
            "version": version,
            "location_kind": location_kind,
            "location_ref": location_ref,
            "update_channel": update_channel,
            "installed_at": now,
            "updated_at": now,
        }
        await self._db.execute(
            """INSERT INTO install_registry
               (id, item_id, item_kind, version, location_kind, location_ref,
                update_channel, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], row["item_id"], row["item_kind"], row["version"],
                row["location_kind"], row["location_ref"], row["update_channel"],
                row["installed_at"], row["updated_at"],
            ),
        )
        await self._db.commit()
        return row

    async def list(
        self, item_id: str | None = None, location_ref: str | None = None
    ) -> list[dict]:
        sql = "SELECT id, item_id, item_kind, version, location_kind, location_ref, update_channel, installed_at, updated_at FROM install_registry"
        params: list = []
        clauses: list[str] = []
        if item_id is not None:
            clauses.append("item_id = ?")
            params.append(item_id)
        if location_ref is not None:
            clauses.append("location_ref = ?")
            params.append(location_ref)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY installed_at ASC"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0], "item_id": r[1], "item_kind": r[2],
                "version": r[3], "location_kind": r[4], "location_ref": r[5],
                "update_channel": r[6], "installed_at": r[7], "updated_at": r[8],
            }
            for r in rows
        ]

    async def get(self, entry_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, item_id, item_kind, version, location_kind, location_ref, update_channel, installed_at, updated_at FROM install_registry WHERE id = ?",
            (entry_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "item_id": row[1], "item_kind": row[2],
            "version": row[3], "location_kind": row[4], "location_ref": row[5],
            "update_channel": row[6], "installed_at": row[7], "updated_at": row[8],
        }

    async def set_version(self, entry_id: str, version: str) -> dict | None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE install_registry SET version = ?, updated_at = ? WHERE id = ?",
            (version, now, entry_id),
        )
        await self._db.commit()
        return await self.get(entry_id)

    async def delete(self, entry_id: str) -> bool:
        row = await self.get(entry_id)
        if row is None:
            return False
        await self._db.execute("DELETE FROM install_registry WHERE id = ?", (entry_id,))
        await self._db.commit()
        return True
