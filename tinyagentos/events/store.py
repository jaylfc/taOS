from __future__ import annotations

import json
from pathlib import Path

from tinyagentos.base_store import BaseStore
from tinyagentos.events.bus import SystemEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS system_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT    NOT NULL,
    source   TEXT    NOT NULL,
    targets  TEXT    NOT NULL,
    level    TEXT    NOT NULL,
    payload  TEXT    NOT NULL,
    ts       REAL    NOT NULL,
    trace_id TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sev_ts   ON system_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_sev_kind ON system_events(kind);
"""


class SystemEventStore(BaseStore):
    SCHEMA = _SCHEMA

    async def add(self, event: SystemEvent) -> None:
        await self._db.execute(
            """INSERT INTO system_events
               (kind, source, targets, level, payload, ts, trace_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.kind,
                event.source,
                json.dumps(event.targets),
                event.level,
                json.dumps(event.payload),
                event.ts,
                event.trace_id,
            ),
        )
        await self._db.commit()

    async def list(self, limit: int = 100, kind: str | None = None) -> list[dict]:
        if kind is not None:
            sql = (
                "SELECT id, kind, source, targets, level, payload, ts, trace_id "
                "FROM system_events WHERE kind = ? ORDER BY ts DESC LIMIT ?"
            )
            params = (kind, limit)
        else:
            sql = (
                "SELECT id, kind, source, targets, level, payload, ts, trace_id "
                "FROM system_events ORDER BY ts DESC LIMIT ?"
            )
            params = (limit,)
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "source": r[2],
                "targets": json.loads(r[3]),
                "level": r[4],
                "payload": json.loads(r[5]),
                "ts": r[6],
                "trace_id": r[7],
            }
            for r in rows
        ]
