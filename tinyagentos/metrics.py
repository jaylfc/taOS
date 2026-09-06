from __future__ import annotations
import json
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore


class MetricsStore(BaseStore):
    SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    timestamp INTEGER NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    labels TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON metrics(name, timestamp);
"""

    async def insert(self, name: str, value: float, timestamp: int | None = None, labels: dict | None = None) -> None:
        ts = timestamp or int(time.time())
        labels_json = json.dumps(labels) if labels else None
        await self._db.execute(
            "INSERT INTO metrics (timestamp, name, value, labels) VALUES (?, ?, ?, ?)",
            (ts, name, value, labels_json),
        )
        await self._db.commit()

    async def query(self, name: str, start: int, end: int, labels: dict | None = None) -> list[dict]:
        sql = "SELECT timestamp, value, labels FROM metrics WHERE name = ? AND timestamp >= ? AND timestamp <= ?"
        params: list = [name, start, end]
        if labels:
            for k, v in labels.items():
                sql += " AND json_extract(labels, ?) = ?"
                params.extend([f"$.{k}", v])
        sql += " ORDER BY timestamp ASC"
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [{"timestamp": r[0], "value": r[1], "labels": json.loads(r[2]) if r[2] else None} for r in rows]

    async def latest(self, name: str, labels: dict | None = None) -> dict | None:
        sql = "SELECT timestamp, value, labels FROM metrics WHERE name = ?"
        params: list = [name]
        if labels:
            for k, v in labels.items():
                sql += " AND json_extract(labels, ?) = ?"
                params.extend([f"$.{k}", v])
        sql += " ORDER BY timestamp DESC LIMIT 1"
        async with self._db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return {"timestamp": row[0], "value": row[1], "labels": json.loads(row[2]) if row[2] else None}

    async def cleanup(self, retention_days: int) -> int:
        cutoff = int(time.time()) - (retention_days * 86400)
        cursor = await self._db.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
        await self._db.commit()
        return cursor.rowcount
