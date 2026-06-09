"""Per-agent hourly-bucketed trace store for the librarian / zero-loss layer.

Every LLM call, tool invocation, and agent message boundary is captured
here. Files live on a dedicated host path that is bind-mounted into the
container at /root/.taos/trace/ — the single persistent trace mount in
the Phase 2 snapshot model (docs/design/architecture-pivot-v2.md §3.3).

Layout:
    {data_dir}/trace/{slug}/
        YYYY-MM-DDTHH.db       # primary: aiosqlite, one per UTC hour
        YYYY-MM-DDTHH.jsonl    # fallback: appended only on DB failure

Bucket routing uses the EVENT's created_at, not wall-clock at write
time, so rollover never drops events — a 14:59:59.999 event routed at
15:00:00.001 lands in the T14 file. The registry keeps the current
bucket open per agent (and the previous hour briefly, for late events);
older buckets close automatically.

Envelope v1 — stable, see the module-level ENVELOPE_V1_SCHEMA dict for
the full shape. Kinds and their payload shapes are documented there.
Consumers (taOSmd librarian) rely on this contract; bump v if it ever
has to change and provide a migration path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from tinyagentos.db_migrations import apply_wal_pragmas_async

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SEAL_AGE_SECONDS = 7200  # 2 hours

TRACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_events (
    id TEXT PRIMARY KEY,
    v INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    trace_id TEXT,
    parent_id TEXT,
    agent_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    channel_id TEXT,
    thread_id TEXT,
    backend_name TEXT,
    model TEXT,
    duration_ms INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    error TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_trace_kind_time ON trace_events(kind, created_at);
CREATE INDEX IF NOT EXISTS idx_trace_trace_id ON trace_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_created ON trace_events(created_at);
"""

VALID_KINDS = frozenset({
    "message_in", "message_out", "llm_call", "tool_call",
    "tool_result", "reasoning", "error", "lifecycle",
    # Phase 1 observability: post-hoc reasoning judge result (§4.7 / §7).
    # Never emitted as an OTel span — it is an internal eval artifact.
    "reasoning_audit",
    # Registry governance audit events (PR1 — lifecycle state machine).
    # Payload shape: {action, canonical_id, actor_user_id, before_status, after_status}
    "governance",
})

# Documented envelope schema — the librarian parses against this.
# Keep this dict in sync with the columns + docstring. If you change
# any field name, bump SCHEMA_VERSION and document the migration.
ENVELOPE_V1_SCHEMA = {
    "version": SCHEMA_VERSION,
    "envelope": [
        "v", "id", "trace_id", "parent_id", "created_at", "agent_name",
        "kind", "channel_id", "thread_id", "backend_name", "model",
        "duration_ms", "tokens_in", "tokens_out", "cost_usd", "error", "payload",
    ],
    "kinds": {
        "message_in": {"from": "str", "text": "str", "attachments": "list?", "content_blocks": "list?"},
        "message_out": {"content": "str", "content_blocks": "list?"},
        "llm_call": {"status": "success|failure", "messages": "list", "response": "any", "metadata": "dict"},
        "tool_call": {"tool": "str", "args": "dict", "caller": "str"},
        "tool_result": {"tool": "str", "result": "any", "success": "bool"},
        "reasoning": {"text": "str", "block_type": "str?"},
        "error": {"stage": "str", "message": "str", "traceback": "str?"},
        "lifecycle": {"event": "str", "reason": "str?"},
        # Phase 1 observability — judge result written by tinyagentos/otel/judge.py (Phase 4).
        # Payload shape: {verdict: "pass"|"warn"|"fail", flags: list[str], model: str, latency_ms: int}
        "reasoning_audit": {"verdict": "pass|warn|fail", "flags": "list[str]", "model": "str", "latency_ms": "int"},
        # Registry governance audit (PR1 — lifecycle state machine).
        "governance": {
            "action": "str",
            "canonical_id": "str",
            "actor_user_id": "str",
            "before_status": "str",
            "after_status": "str",
        },
    },
}


def _bucket_key(ts: float) -> str:
    """UTC hour bucket: ``YYYY-MM-DDTHH``. Deterministic from the event's
    created_at so rollover never drops events."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H")


def _agent_trace_dir(data_dir: Path, slug: str) -> Path:
    return data_dir / "trace" / slug


def _bucket_db_path(data_dir: Path, slug: str, bucket: str) -> Path:
    return _agent_trace_dir(data_dir, slug) / f"{bucket}.db"


def _bucket_jsonl_path(data_dir: Path, slug: str, bucket: str) -> Path:
    return _agent_trace_dir(data_dir, slug) / f"{bucket}.jsonl"


def _bucket_late_jsonl_path(data_dir: Path, slug: str, bucket: str) -> Path:
    return _agent_trace_dir(data_dir, slug) / f"{bucket}.late.jsonl"


def _new_id() -> str:
    return uuid.uuid4().hex


def _build_envelope(agent_name: str, kind: str, fields: dict) -> dict:
    """Produce a v1 envelope from loose input, filling ids + created_at.

    Timing convenience: callers may pass ``ts_start`` (float Unix epoch) instead
    of a pre-computed ``duration_ms``. When ``ts_start`` is present and
    ``duration_ms`` is absent, ``duration_ms`` is computed as
    ``int((time.time() - ts_start) * 1000)``. Callers that already supply
    ``duration_ms`` directly are unaffected. ``ts_start`` itself is NOT
    persisted as a column or stored in the payload.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown kind: {kind!r}; valid: {sorted(VALID_KINDS)}")
    duration_ms = fields.get("duration_ms")
    if duration_ms is None and fields.get("ts_start") is not None:
        duration_ms = max(0, int((time.time() - fields["ts_start"]) * 1000))
    return {
        "v": SCHEMA_VERSION,
        "id": fields.get("id") or _new_id(),
        "trace_id": fields.get("trace_id"),
        "parent_id": fields.get("parent_id"),
        "created_at": fields.get("created_at") or time.time(),
        "agent_name": agent_name,
        "kind": kind,
        "channel_id": fields.get("channel_id"),
        "thread_id": fields.get("thread_id"),
        "backend_name": fields.get("backend_name"),
        "model": fields.get("model"),
        "duration_ms": duration_ms,
        "tokens_in": fields.get("tokens_in"),
        "tokens_out": fields.get("tokens_out"),
        "cost_usd": fields.get("cost_usd"),
        "error": fields.get("error"),
        "payload": fields.get("payload") or {},
    }


class AgentTraceStore:
    """One agent's trace store — manages hourly bucket DBs + JSONL fallback."""

    def __init__(self, data_dir: Path, slug: str):
        self._data_dir = data_dir
        self._slug = slug
        # bucket_key -> aiosqlite.Connection
        self._connections: dict[str, aiosqlite.Connection] = {}
        self._lock = asyncio.Lock()
        # Optional OTel emitter injected by the app lifespan.
        # None = no-op (tests, early startup, or no receiver configured).
        self._emitter: object | None = None  # type: tinyagentos.otel.emitter.OTelEmitter
        # Optional reasoning judge (Phase 4). Fired on lifecycle session_end.
        # None = no-op (tests, or judge not configured).
        self._judge: object | None = None  # type: tinyagentos.otel.judge.ReasoningJudge

    @property
    def slug(self) -> str:
        return self._slug

    def set_emitter(self, emitter: object | None) -> None:
        """Inject the OTel emitter.  None disables emission."""
        self._emitter = emitter

    def set_judge(self, judge: object | None) -> None:
        """Inject the reasoning judge.  None disables judging."""
        self._judge = judge

    async def _open_bucket(self, bucket: str) -> aiosqlite.Connection:
        conn = self._connections.get(bucket)
        if conn is not None:
            return conn
        trace_dir = _agent_trace_dir(self._data_dir, self._slug)
        trace_dir.mkdir(parents=True, exist_ok=True)
        try:
            trace_dir.chmod(0o700)
        except OSError:
            pass
        db_path = _bucket_db_path(self._data_dir, self._slug, bucket)
        conn = await aiosqlite.connect(str(db_path))
        await apply_wal_pragmas_async(conn)
        await conn.executescript(TRACE_SCHEMA)
        await conn.commit()
        self._connections[bucket] = conn
        return conn

    async def _evict_old_buckets(self, current_bucket: str) -> None:
        """Close buckets older than current - 2 hours. Keep current and
        previous bucket open for late-arriving events. Seals files for
        buckets old enough to be considered immutable."""
        keep = {current_bucket}
        try:
            from datetime import timedelta
            dt = datetime.strptime(current_bucket, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
            prev = (dt - timedelta(hours=1)).strftime("%Y-%m-%dT%H")
            keep.add(prev)
        except ValueError:
            pass
        now = time.time()
        for b in list(self._connections.keys()):
            if b in keep:
                continue
            try:
                await self._connections[b].close()
            except Exception:
                pass
            del self._connections[b]
            # Seal if the bucket is old enough.
            try:
                bucket_ts = datetime.strptime(b, "%Y-%m-%dT%H").replace(
                    tzinfo=timezone.utc
                ).timestamp()
                if now - bucket_ts >= SEAL_AGE_SECONDS:
                    self._seal_bucket(b)
            except ValueError:
                pass

    async def close(self) -> None:
        async with self._lock:
            for c in self._connections.values():
                try:
                    await c.close()
                except Exception:
                    pass
            self._connections.clear()

    async def record(self, kind: str, **fields) -> dict:
        """Record an event. Returns the written envelope (with id/created_at
        filled). On SQLite failure, falls back to JSONL in the same bucket.

        Caller can supply ``id`` for idempotency; if omitted, a fresh uuid
        is minted. Unknown kinds raise ValueError — catch in the route."""
        envelope = _build_envelope(self._slug, kind, fields)
        bucket = _bucket_key(envelope["created_at"])
        async with self._lock:
            # Route sealed buckets straight to the late sidecar — attempting
            # SQLite on a read-only file would raise "readonly database".
            if self._is_sealed_bucket(bucket):
                self._append_late(bucket, envelope)
                now_bucket = _bucket_key(time.time())
                await self._evict_old_buckets(now_bucket)
                return envelope
            try:
                conn = await self._open_bucket(bucket)
                await conn.execute(
                    """INSERT OR IGNORE INTO trace_events
                       (id, v, created_at, trace_id, parent_id, agent_name, kind,
                        channel_id, thread_id, backend_name, model, duration_ms,
                        tokens_in, tokens_out, cost_usd, error, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        envelope["id"], envelope["v"], envelope["created_at"],
                        envelope["trace_id"], envelope["parent_id"], envelope["agent_name"],
                        envelope["kind"], envelope["channel_id"], envelope["thread_id"],
                        envelope["backend_name"], envelope["model"], envelope["duration_ms"],
                        envelope["tokens_in"], envelope["tokens_out"], envelope["cost_usd"],
                        envelope["error"], json.dumps(envelope["payload"], default=str),
                    ),
                )
                await conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "trace_store: DB write failed for %s/%s — appending to JSONL (%s)",
                    self._slug, bucket, exc,
                )
                self._append_fallback(bucket, envelope)
            # Opportunistic eviction — cheap, runs under our lock.
            now_bucket = _bucket_key(time.time())
            await self._evict_old_buckets(now_bucket)
        # OTel emission is a side-effect of record() — D5: NOT routed via bus.py.
        # Fire-and-forget; any emission error is silently dropped.
        if self._emitter is not None:
            try:
                self._emitter.emit(envelope)
            except Exception:
                pass
        # Phase 4: reasoning judge fires on session_end (non-trivial runs only).
        if (
            self._judge is not None
            and kind == "lifecycle"
            and envelope.get("trace_id")
            and isinstance(envelope.get("payload"), dict)
            and envelope["payload"].get("event") == "session_end"
        ):
            try:
                self._judge.schedule(self, envelope["trace_id"])
            except Exception:
                pass
        return envelope

    def _seal_bucket(self, bucket: str) -> None:
        """chmod .db and .jsonl to 0o400 (read-only) for the given bucket.
        Idempotent — skips files that are already read-only."""
        db_path = _bucket_db_path(self._data_dir, self._slug, bucket)
        jsonl_path = _bucket_jsonl_path(self._data_dir, self._slug, bucket)
        for path in (db_path, jsonl_path):
            if not path.exists():
                continue
            try:
                if not os.access(str(path), os.W_OK):
                    # Already read-only; nothing to do.
                    continue
                os.chmod(str(path), 0o400)
                logger.info(
                    "trace_store: sealed bucket file %s", path
                )
            except OSError as exc:
                logger.warning(
                    "trace_store: could not seal %s: %s", path, exc
                )

    def _is_sealed_bucket(self, bucket: str) -> bool:
        """Return True if the bucket's .db file exists and is read-only."""
        db_path = _bucket_db_path(self._data_dir, self._slug, bucket)
        if not db_path.exists():
            return False
        return not os.access(str(db_path), os.W_OK)

    def _append_late(self, bucket: str, envelope: dict) -> None:
        """Write an envelope to the late-arrival sidecar ({bucket}.late.jsonl).
        This file is always writable and never sealed."""
        try:
            path = _bucket_late_jsonl_path(self._data_dir, self._slug, bucket)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                path.parent.chmod(0o700)
            except OSError:
                pass
            with open(path, "a") as f:
                f.write(json.dumps(envelope, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "trace_store: LATE WRITE FAILED for %s/%s: %s — EVENT LOST",
                self._slug, bucket, exc,
            )

    def _append_fallback(self, bucket: str, envelope: dict) -> None:
        try:
            path = _bucket_jsonl_path(self._data_dir, self._slug, bucket)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(json.dumps(envelope, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "trace_store: FALLBACK WRITE FAILED for %s/%s: %s — EVENT LOST",
                self._slug, bucket, exc,
            )

    async def list(
        self,
        *,
        kind: str | None = None,
        channel_id: str | None = None,
        trace_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Read events newest-first across all bucket files the agent has.

        Merges SQLite rows + JSONL fallback lines sorted by created_at
        DESC. ``limit`` applies AFTER merge + sort so the caller gets a
        faithful newest-first slice even if the fallback captured an
        event while the DB was down. Bounded at 1000 to protect memory."""
        limit = max(1, min(limit, 1000))
        trace_dir = _agent_trace_dir(self._data_dir, self._slug)
        if not trace_dir.exists():
            return []
        merged: list[dict] = []
        # Track seen ids so duplicates (same event in .db and .late.jsonl)
        # are deduplicated; the first occurrence wins (primary source = DB).
        seen_ids: set[str] = set()

        # Enumerate bucket files; filter by since/until on bucket keys
        # where possible before opening each one.
        db_files = sorted(trace_dir.glob("*.db"), reverse=True)
        # Collect all .jsonl files, including .late.jsonl sidecars.
        jsonl_files = sorted(trace_dir.glob("*.jsonl"), reverse=True)
        late_jsonl_files = sorted(trace_dir.glob("*.late.jsonl"), reverse=True)

        async with self._lock:
            for db_file in db_files:
                bucket = db_file.stem
                if not _bucket_overlaps(bucket, since, until):
                    continue
                try:
                    conn = await self._open_bucket(bucket)
                    clauses = []
                    params: list[Any] = []
                    if kind is not None:
                        clauses.append("kind = ?"); params.append(kind)
                    if channel_id is not None:
                        clauses.append("channel_id = ?"); params.append(channel_id)
                    if trace_id is not None:
                        clauses.append("trace_id = ?"); params.append(trace_id)
                    if since is not None:
                        clauses.append("created_at >= ?"); params.append(since)
                    if until is not None:
                        clauses.append("created_at <= ?"); params.append(until)
                    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                    sql = f"SELECT * FROM trace_events{where} ORDER BY created_at DESC LIMIT ?"
                    params.append(limit)
                    cur = await conn.execute(sql, params)
                    rows = await cur.fetchall()
                    cols = [d[0] for d in cur.description]
                    await cur.close()
                    for r in rows:
                        ev = dict(zip(cols, r))
                        try:
                            ev["payload"] = json.loads(ev.get("payload") or "{}")
                        except Exception:
                            pass
                        ev_id = ev.get("id")
                        if ev_id and ev_id in seen_ids:
                            continue
                        if ev_id:
                            seen_ids.add(ev_id)
                        merged.append(ev)
                except Exception as exc:
                    logger.warning("trace_store: list() bucket %s skipped: %s", bucket, exc)
                if len(merged) >= limit * 4:
                    # Enough to sort + trim; don't open more buckets.
                    break

            def _read_jsonl_file(jl: Path, bucket: str) -> None:
                if not _bucket_overlaps(bucket, since, until):
                    return
                try:
                    with open(jl) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if kind is not None and ev.get("kind") != kind:
                                continue
                            if channel_id is not None and ev.get("channel_id") != channel_id:
                                continue
                            if trace_id is not None and ev.get("trace_id") != trace_id:
                                continue
                            ts = ev.get("created_at") or 0
                            if since is not None and ts < since:
                                continue
                            if until is not None and ts > until:
                                continue
                            ev_id = ev.get("id")
                            if ev_id and ev_id in seen_ids:
                                continue
                            if ev_id:
                                seen_ids.add(ev_id)
                            merged.append(ev)
                except Exception as exc:
                    logger.warning("trace_store: list() jsonl %s skipped: %s", jl, exc)

            # Merge regular JSONL fallback lines (not late sidecars).
            for jl in jsonl_files:
                # Skip late sidecars here; they are handled separately below.
                if jl.name.endswith(".late.jsonl"):
                    continue
                _read_jsonl_file(jl, jl.name[: -len(".jsonl")])

            # Merge late-arrival sidecars.
            for jl in late_jsonl_files:
                # Bucket key is the stem minus ".late" suffix.
                bucket = jl.name[: -len(".late.jsonl")]
                _read_jsonl_file(jl, bucket)

        merged.sort(key=lambda e: e.get("created_at") or 0, reverse=True)
        return merged[:limit]


def _bucket_overlaps(bucket: str, since: float | None, until: float | None) -> bool:
    """True if the hour covered by this bucket could contain events in
    [since, until]. Used to skip opening irrelevant bucket files."""
    try:
        dt = datetime.strptime(bucket, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return True  # Non-standard name — don't skip, let the normal filter reject.
    start = dt.timestamp()
    end = start + 3600
    if since is not None and end < since:
        return False
    if until is not None and start > until:
        return False
    return True


class TraceStoreRegistry:
    """Opens and caches one AgentTraceStore per slug. The only way routes
    should reach per-agent trace DBs."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._stores: dict[str, AgentTraceStore] = {}
        self._lock = asyncio.Lock()
        self._emitter: object | None = None
        self._judge: object | None = None

    def set_emitter(self, emitter: object | None) -> None:
        """Inject the OTel emitter into all current and future stores."""
        self._emitter = emitter
        for store in self._stores.values():
            store.set_emitter(emitter)

    def set_judge(self, judge: object | None) -> None:
        """Inject the reasoning judge into all current and future stores."""
        self._judge = judge
        for store in self._stores.values():
            store.set_judge(judge)

    async def get(self, slug: str) -> AgentTraceStore:
        async with self._lock:
            store = self._stores.get(slug)
            if store is None:
                store = AgentTraceStore(self._data_dir, slug)
                store.set_emitter(self._emitter)
                store.set_judge(self._judge)
                self._stores[slug] = store
            return store

    async def forget(self, slug: str) -> None:
        """Close & drop the cached handle. Called on archive/purge so
        the bucket files aren't held open while the home folder moves."""
        async with self._lock:
            store = self._stores.pop(slug, None)
            if store is not None:
                await store.close()

    async def close_all(self) -> None:
        async with self._lock:
            for s in self._stores.values():
                await s.close()
            self._stores.clear()
