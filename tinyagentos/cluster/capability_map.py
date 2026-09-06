from __future__ import annotations

import json
import time

from tinyagentos.base_store import BaseStore

CAPABILITY_MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_map (
    node_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL DEFAULT '',
    cpu TEXT NOT NULL DEFAULT '{}',
    ram_mb INTEGER NOT NULL DEFAULT 0,
    gpu TEXT NOT NULL DEFAULT '{}',
    npu TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'offline',
    last_seen INTEGER NOT NULL DEFAULT 0
);
"""

VALID_STATUS = {"online", "offline", "draining"}
_COLS = "node_id, hostname, cpu, ram_mb, gpu, npu, status, last_seen"


def _row(r) -> dict:
    return {
        "node_id": r[0],
        "hostname": r[1],
        "cpu": json.loads(r[2] or "{}"),
        "ram_mb": r[3],
        "gpu": json.loads(r[4] or "{}"),
        "npu": json.loads(r[5] or "{}"),
        "status": r[6],
        "last_seen": r[7],
    }


class CapabilityMap(BaseStore):
    """Per-node hardware capability map for the cluster.

    Records each node's CPU/GPU/NPU/RAM plus a live status (online/offline/
    draining) and last_seen heartbeat. The foundation the scheduler and
    placement logic read from. CPU/GPU/NPU are stored as JSON columns so the
    shapes match HardwareProfile without a rigid schema.
    """

    SCHEMA = CAPABILITY_MAP_SCHEMA

    async def upsert(self, node: dict) -> dict:
        node_id = node.get("node_id")
        if not node_id:
            raise ValueError("node requires a non-empty node_id")
        status = node.get("status", "offline")
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status!r}")
        ls = node.get("last_seen")
        last_seen = int(ls) if ls is not None else int(time.time())
        await self._db.execute(
            f"""INSERT INTO capability_map ({_COLS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    hostname=excluded.hostname, cpu=excluded.cpu,
                    ram_mb=excluded.ram_mb, gpu=excluded.gpu, npu=excluded.npu,
                    status=excluded.status, last_seen=excluded.last_seen""",
            (
                node_id, node.get("hostname", ""), json.dumps(node.get("cpu", {})),
                int(node.get("ram_mb", 0)), json.dumps(node.get("gpu", {})),
                json.dumps(node.get("npu", {})), status, last_seen,
            ),
        )
        await self._db.commit()
        return await self.get(node_id)

    async def get(self, node_id: str) -> dict | None:
        async with self._db.execute(
            f"SELECT {_COLS} FROM capability_map WHERE node_id = ?", (node_id,)
        ) as cur:
            r = await cur.fetchone()
        return _row(r) if r else None

    async def list(self, status: str | None = None) -> list[dict]:
        sql = f"SELECT {_COLS} FROM capability_map"
        params: list = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY node_id ASC"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def set_status(self, node_id: str, status: str) -> dict | None:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status!r}")
        if await self.get(node_id) is None:
            return None
        await self._db.execute(
            "UPDATE capability_map SET status = ? WHERE node_id = ?", (status, node_id)
        )
        await self._db.commit()
        return await self.get(node_id)

    async def prune_stale(self, older_than_s: int) -> int:
        cutoff = int(time.time()) - older_than_s
        async with self._db.execute(
            "DELETE FROM capability_map WHERE last_seen < ?", (cutoff,)
        ) as cur:
            count = cur.rowcount
        await self._db.commit()
        return count

    async def mark_stale_offline(self, older_than_s: int) -> int:
        """Mark nodes that stopped heartbeating as offline (row preserved).

        The non-destructive counterpart to prune_stale: an 'online' node whose
        last_seen is older than the cutoff is flipped to 'offline' so the
        scheduler stops placing work on it, but the row (and its hardware) stays
        for history and for when it comes back. 'draining' is an admin intent
        and is left untouched; only 'online' nodes go stale-offline. Returns the
        number of nodes flipped.
        """
        cutoff = int(time.time()) - older_than_s
        async with self._db.execute(
            "UPDATE capability_map SET status = 'offline' "
            "WHERE status = 'online' AND last_seen < ?",
            (cutoff,),
        ) as cur:
            count = cur.rowcount
        await self._db.commit()
        return count
