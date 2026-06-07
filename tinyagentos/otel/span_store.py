"""Per-agent OTel span store — Phase 1 foundation.

Stores OpenTelemetry GenAI spans received from the local emitter (Phase 2)
and from taOSmd (when configured). Each agent gets its own SQLite database at:

    {data_dir}/trace/{slug}/otel-spans.db

System spans (no agent) land in:

    {data_dir}/trace/_system/otel-spans.db

Schema: otel_spans(span_id PK, trace_id, parent_span_id, name,
        start_time_ns, end_time_ns, attributes JSON, status_code,
        agent_name, conversation_id, created_at)

Design: WAL mode for concurrent readers (receiver + query route). Uses
aiosqlite to stay on the async event loop, matching the rest of the codebase.
#653's WAL helpers are not yet merged to dev; this module sets the PRAGMA
directly on open.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS otel_spans (
    span_id          TEXT PRIMARY KEY,
    trace_id         TEXT NOT NULL,
    parent_span_id   TEXT,
    name             TEXT NOT NULL,
    start_time_ns    INTEGER NOT NULL,
    end_time_ns      INTEGER NOT NULL,
    attributes       TEXT NOT NULL DEFAULT '{}',
    status_code      TEXT,
    agent_name       TEXT,
    conversation_id  TEXT,
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_otelspan_trace_id
    ON otel_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_otelspan_agent_created
    ON otel_spans(agent_name, created_at);
CREATE INDEX IF NOT EXISTS idx_otelspan_conversation_id
    ON otel_spans(conversation_id);
"""


def _span_db_path(data_dir: Path, slug: str) -> Path:
    """Resolve the otel-spans.db path for a given agent slug."""
    return data_dir / "trace" / slug / "otel-spans.db"


class SpanStore:
    """Manages a single otel-spans.db for one agent (or _system)."""

    def __init__(self, data_dir: Path, slug: str):
        self._db_path = _span_db_path(data_dir, slug)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _ensure_open(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._db_path.parent.chmod(0o700)
        except OSError:
            pass
        conn = await aiosqlite.connect(str(self._db_path))
        await conn.executescript(_SCHEMA)
        await conn.commit()
        self._conn = conn
        return conn

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                try:
                    await self._conn.close()
                except Exception:
                    pass
                self._conn = None

    async def write_span(
        self,
        *,
        span_id: str,
        trace_id: str,
        parent_span_id: str | None,
        name: str,
        start_time_ns: int,
        end_time_ns: int,
        attributes: dict | None = None,
        status_code: str | None = None,
        agent_name: str | None = None,
        conversation_id: str | None = None,
        created_at: float | None = None,
    ) -> None:
        """Persist one OTel span. Idempotent (INSERT OR IGNORE on span_id PK)."""
        attrs_json = json.dumps(attributes or {}, default=str)
        ts = created_at if created_at is not None else time.time()
        async with self._lock:
            conn = await self._ensure_open()
            await conn.execute(
                """INSERT OR IGNORE INTO otel_spans
                   (span_id, trace_id, parent_span_id, name,
                    start_time_ns, end_time_ns, attributes,
                    status_code, agent_name, conversation_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    span_id, trace_id, parent_span_id, name,
                    start_time_ns, end_time_ns, attrs_json,
                    status_code, agent_name, conversation_id, ts,
                ),
            )
            await conn.commit()

    async def query_spans(
        self,
        agent_name: str | None = None,
        *,
        since: float | None = None,
        until: float | None = None,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return spans newest-first, filtered by any combination of
        agent_name / trace_id / time window. limit is capped at 1000."""
        limit = max(1, min(limit, 1000))
        clauses: list[str] = []
        params: list[Any] = []
        if agent_name is not None:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        if trace_id is not None:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM otel_spans{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        async with self._lock:
            conn = await self._ensure_open()
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
            await cur.close()
        result = []
        for row in rows:
            span = dict(zip(cols, row))
            try:
                span["attributes"] = json.loads(span.get("attributes") or "{}")
            except Exception:
                pass
            result.append(span)
        return result


class SpanStoreRegistry:
    """Opens and caches one SpanStore per agent slug."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._stores: dict[str, SpanStore] = {}
        self._lock = asyncio.Lock()

    async def get(self, slug: str) -> SpanStore:
        async with self._lock:
            store = self._stores.get(slug)
            if store is None:
                store = SpanStore(self._data_dir, slug)
                self._stores[slug] = store
            return store

    async def close_all(self) -> None:
        async with self._lock:
            for s in self._stores.values():
                await s.close()
            self._stores.clear()
