"""Per-agent hourly-bucketed trace store for the librarian / zero-loss layer.

Every LLM call, tool invocation, and agent message boundary is captured
here. Files live on a dedicated host path that is bind-mounted into the
container at /root/.taos/trace/ -- the single persistent trace mount in
the Phase 2 snapshot model (docs/design/architecture-pivot-v2.md §3.3).

Layout:
    {data_dir}/trace/{slug}/
        YYYY-MM-DDTHH.db       # primary: aiosqlite, one per UTC hour
        YYYY-MM-DDTHH.jsonl    # fallback: appended only on DB failure

Bucket routing uses the EVENT's created_at, not wall-clock at write
time, so rollover never drops events -- a 14:59:59.999 event routed at
15:00:00.001 lands in the T14 file. The registry keeps the current
bucket open per agent (and the previous hour briefly, for late events);
older buckets close automatically.

Envelope v1 -- stable, see the module-level ENVELOPE_V1_SCHEMA dict for
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
from tinyagentos.otel.span_store import _safe_slug

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
CREATE VIRTUAL TABLE IF NOT EXISTS trace_events_fts USING fts5(id UNINDEXED, text);
"""

# Maximum searchable text length per event (64KB). Payloads are rendered
# into a single string before being written to trace_events_fts.
FTS_TEXT_CAP = 65536

# Payload keys rendered into the FTS text column, in order.
FTS_PAYLOAD_KEYS = ("text", "content", "tool", "args", "result")

VALID_KINDS = frozenset({
    "message_in", "message_out", "llm_call", "tool_call",
    "tool_result", "reasoning", "error", "lifecycle",
    # Phase 1 observability: post-hoc reasoning judge result (§4.7 / §7).
    # Never emitted as an OTel span -- it is an internal eval artifact.
    "reasoning_audit",
    # Registry governance audit events (PR1 -- lifecycle state machine).
    # Payload shape: {action, canonical_id, actor_user_id, before_status, after_status}
    "governance",
    # GPU work queue per-op audit record (taOS #1864). One per op on
    # completion, plus one on eviction/cancel.
    "gpu_op",
    # taOS Agent audit (full-authority spec section 4): one row per gated
    # action request and one per outcome, written by the action gate only.
    "external_write",
    # taOS Agent reversible local fix with its registered undo.
    "local_fix",
})

# Kinds whose payload is an audit record: every listed field is required and
# ``confirmed_by`` must be one of the allowed values. A row that does not
# match is refused (ValueError) rather than stored half-filled.
REQUIRED_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "external_write": (
        "target", "action", "user_id", "repo", "url", "diff_sha256",
        "confirmed_by", "action_id", "latency_ms", "state",
    ),
    "local_fix": ("tool", "target", "undo", "user_id"),
}
EXTERNAL_WRITE_CONFIRMED_BY = frozenset({"user", "auto", "declined", "pending"})
# Written only by taOS itself (the action gate, the local-fix tools), never
# accepted from POST /api/trace, so a caller cannot forge its own audit trail.
SERVER_ONLY_KINDS = frozenset({"external_write", "local_fix"})

# Documented envelope schema -- the librarian parses against this.
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
        # Phase 1 observability -- judge result written by tinyagentos/otel/judge.py (Phase 4).
        # Payload shape: {verdict: "pass"|"warn"|"fail", flags: list[str], model: str, latency_ms: int}
        "reasoning_audit": {"verdict": "pass|warn|fail", "flags": "list[str]", "model": "str", "latency_ms": "int"},
        # Registry governance audit (PR1 -- lifecycle state machine).
        "governance": {
            "action": "str",
            "canonical_id": "str",
            "actor_user_id": "str",
            "before_status": "str",
            "after_status": "str",
        },
        # GPU work queue per-op audit record (taOS #1864). One per op on
        # completion, plus one on eviction/cancel. model/backend_name/
        # duration_ms ride the first-class columns.
        "gpu_op": {
            "op": "inference|load|unload|evict",
            "outcome": "ok|error|cancelled|evicted|shadow_denied",
            "wait_ms": "int", "queue_position_at_enqueue": "int",
            "queue_depth_at_admit": "int", "required_vram_mb": "int",
            "free_vram_mb_at_admit": "int", "reserved_vram_mb_at_admit": "int",
            "resident_models_at_admit": "int",
            "evictions_triggered": "list[str]", "priority": "int",
            "submitter": "str",
        },
        # taOS Agent audit (full-authority spec section 4). A request row has
        # state "pending" (confirmed_by "pending") or "confirmed" under the
        # auto policy (confirmed_by "auto"); the outcome row has state
        # "executed", "declined" or "failed".
        "external_write": {
            "target": "str", "action": "str", "user_id": "str", "repo": "str",
            "url": "str", "diff_sha256": "str",
            "confirmed_by": "user|auto|declined|pending", "action_id": "str",
            "latency_ms": "int", "state": "pending|confirmed|executed|declined|failed",
        },
        "local_fix": {"tool": "str", "target": "str", "undo": "str", "user_id": "str"},
    },
}


def _validate_payload(kind: str, payload: object) -> None:
    required = REQUIRED_PAYLOAD_FIELDS.get(kind)
    if required is None:
        return
    if not isinstance(payload, dict):
        raise ValueError(f"{kind} payload must be a dict")
    missing = [f for f in required if f not in payload]
    if missing:
        raise ValueError(f"{kind} payload missing required field(s): {', '.join(missing)}")
    if kind == "external_write" and payload["confirmed_by"] not in EXTERNAL_WRITE_CONFIRMED_BY:
        raise ValueError(
            f"external_write confirmed_by must be one of {sorted(EXTERNAL_WRITE_CONFIRMED_BY)}"
        )


def _bucket_key(ts: float) -> str:
    """UTC hour bucket: ``YYYY-MM-DDTHH``. Deterministic from the event's
    created_at so rollover never drops events."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H")


def _agent_trace_dir(data_dir: Path, slug: str) -> Path:
    if not slug or slug in {".", ".."} or "/" in slug or "\\" in slug:
        raise ValueError(f"invalid agent slug {slug!r}: must match [a-z0-9._-]+")
    safe = _safe_slug(slug)
    if safe == "_system":
        raise ValueError(f"invalid agent slug {slug!r}: must match [a-z0-9._-]+")
    return (data_dir / "trace" / safe).resolve()


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
    _validate_payload(kind, fields.get("payload") or {})
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


def _render_fts_text(envelope: dict) -> str:
    """Render an envelope's searchable text for the FTS5 index.

    Concatenates the payload's text/content/tool/args/result fields plus the
    top-level kind and error, capped at FTS_TEXT_CAP bytes. Idempotent for the
    same envelope -- the same input always produces the same string."""
    parts: list[str] = []
    kind = envelope.get("kind")
    if kind:
        parts.append(str(kind))
    error = envelope.get("error")
    if error:
        parts.append(str(error))
    payload = envelope.get("payload")
    if isinstance(payload, dict):
        for key in FTS_PAYLOAD_KEYS:
            val = payload.get(key)
            if val is None:
                continue
            parts.append(str(val))
    text = " ".join(p for p in parts if p != "")
    return text[:FTS_TEXT_CAP]


def _envelope_to_row(envelope: dict) -> tuple:
    """Project a v1 envelope onto the trace_events column tuple.

    Order matches the INSERT column list in record() / import_envelopes()."""
    return (
        envelope["id"], envelope["v"], envelope["created_at"],
        envelope["trace_id"], envelope["parent_id"], envelope["agent_name"],
        envelope["kind"], envelope["channel_id"], envelope["thread_id"],
        envelope["backend_name"], envelope["model"], envelope["duration_ms"],
        envelope["tokens_in"], envelope["tokens_out"], envelope["cost_usd"],
        envelope["error"], json.dumps(envelope["payload"], default=str),
    )


def _fts_phrase_query(query: str) -> str:
    """Convert a plain-text query into an FTS5 phrase query.

    Splits on whitespace, wraps each term in double quotes with internal
    double quotes escaped by doubling, and joins with a space
    (implicit AND). Empty or whitespace-only input returns an empty string.
    """
    terms = query.strip().split()
    if not terms:
        return ""
    escaped = [f'"{term.replace(chr(34), chr(34)*2)}"' for term in terms]
    return " ".join(escaped)


class AgentTraceStore:
    """One agent's trace store -- manages hourly bucket DBs + JSONL fallback."""

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

    async def _open_bucket(self, bucket: str, *, read_only: bool = False) -> aiosqlite.Connection:
        # Only cache non-read-only connections (write connections)
        if not read_only:
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
        # Open read-only connection if requested
        if read_only:
            conn = await aiosqlite.connect(str(db_path), mode='ro')
        else:
            conn = await aiosqlite.connect(str(db_path))
        await apply_wal_pragmas_async(conn)
        await conn.executescript(TRACE_SCHEMA)
        await conn.commit()
        if not read_only:
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
        is minted. Unknown kinds raise ValueError -- catch in the route."""
        envelope = _build_envelope(self._slug, kind, fields)
        bucket = _bucket_key(envelope["created_at"])
        async with self._lock:
            # Route sealed buckets straight to the late sidecar -- attempting
            # SQLite on a read-only file would raise "readonly database".
            if self._is_sealed_bucket(bucket):
                self._append_late(bucket, envelope)
                now_bucket = _bucket_key(time.time())
                await self._evict_old_buckets(now_bucket)
                return envelope
            try:
                conn = await self._open_bucket(bucket)
                await self._insert_envelope(conn, envelope)
                await conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "trace_store: DB write failed for %s/%s -- appending to JSONL (%s)",
                    self._slug, bucket, exc,
                )
                self._append_fallback(bucket, envelope)
            # Opportunistic eviction -- cheap, runs under our lock.
            now_bucket = _bucket_key(time.time())
            await self._evict_old_buckets(now_bucket)
        # OTel emission is a side-effect of record() -- D5: NOT routed via bus.py.
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

    async def _insert_envelope(self, conn, envelope: dict) -> bool:
        """Insert one envelope into the bucket DB + FTS index.

        Returns True if the row was inserted, False if it was a duplicate
        (INSERT OR IGNORE on id). Does NOT commit -- the caller batches."""
        row = _envelope_to_row(envelope)
        cur = await conn.execute(
            """INSERT OR IGNORE INTO trace_events
               (id, v, created_at, trace_id, parent_id, agent_name, kind,
                channel_id, thread_id, backend_name, model, duration_ms,
                tokens_in, tokens_out, cost_usd, error, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        inserted = cur.rowcount == 1
        await cur.close()
        if inserted:
            fts_text = _render_fts_text(envelope)
            cur = await conn.execute(
                "INSERT OR IGNORE INTO trace_events_fts (id, text) VALUES (?, ?)",
                (envelope["id"], fts_text),
            )
            await cur.close()
        return inserted

    async def import_envelopes(self, envelopes: list[dict]) -> dict:
        """Bulk-import pre-built v1 envelopes into the bucket DBs.

        Each envelope is validated with the same rules record() uses (kind in
        VALID_KINDS, v1 keys present), grouped by bucket (_bucket_key of
        created_at), and inserted with INSERT OR IGNORE on id so re-running an
        import is idempotent. Does NOT seal/unseal buckets and does NOT touch
        the live registry bucket -- historical import lands in the DB by
        construction, never in the .late.jsonl fallback.

        Returns {inserted, duplicates, buckets}."""
        inserted = 0
        duplicates = 0
        buckets: list[str] = []
        if not envelopes:
            return {"inserted": 0, "duplicates": 0, "buckets": []}

        # Validate + group up front so a bad envelope fails before any write.
        grouped: dict[str, list[dict]] = {}
        for env in envelopes:
            self._validate_envelope(env)
            bucket = _bucket_key(env["created_at"])
            grouped.setdefault(bucket, []).append(env)
            if bucket not in buckets:
                buckets.append(bucket)

        # Before any write, check sealed buckets for missing ids.
        # A sealed bucket has .db file that is not writable.
        # NOTE: This pre-check runs outside self._lock (TOCTOU window). A bucket
        # sealed concurrently after the check but before the write under the lock
        # could still be written via the cached writable connection from
        # _open_bucket. Acceptable per card spec; the check is a best-effort
        # guard to catch the common case of pre-sealed buckets.
        sealed_buckets_missing: dict[str, list[str]] = {}
        existing_ids_by_bucket: dict[str, set[str]] = {}
        for bucket in buckets:
            db_path = _bucket_db_path(self._data_dir, self._slug, bucket)
            if db_path.exists() and not os.access(str(db_path), os.W_OK):
                # Sealed bucket: open read-only and collect existing ids.
                existing_ids: set[str] = set()
                try:
                    conn = await aiosqlite.connect(
                        f"file:{db_path}?mode=ro",
                        uri=True,
                    )
                    try:
                        cur = await conn.execute("SELECT id FROM trace_events")
                        rows = await cur.fetchall()
                        await cur.close()
                        for row in rows:
                            existing_ids.add(row[0])
                    finally:
                        await conn.close()
                except Exception as exc:
                    logger.warning(
                        "trace_store: import_envelopes could not read sealed bucket %s: %s",
                        bucket, exc,
                    )
                existing_ids_by_bucket[bucket] = existing_ids
                # Check which envelopes for this bucket are missing.
                missing = [env["id"] for env in grouped[bucket] if env["id"] not in existing_ids]
                if missing:
                    sealed_buckets_missing[bucket] = missing

        if sealed_buckets_missing:
            # Format error message with bucket names and missing ids.
            details = ", ".join(
                f"{b}: {len(ids)} missing" for b, ids in sealed_buckets_missing.items()
            )
            raise ValueError(
                f"import_envelopes: sealed buckets have new envelopes (would require write): {details}"
            )

        async with self._lock:
            for bucket, envs in grouped.items():
                # Skip envelopes whose ids already exist in a sealed bucket.
                existing_ids = existing_ids_by_bucket.get(bucket, set())
                envs_to_write = [env for env in envs if env["id"] not in existing_ids]
                duplicates += len(envs) - len(envs_to_write)

                if not envs_to_write:
                    continue

                conn = await self._open_bucket(bucket)
                for env in envs_to_write:
                    if await self._insert_envelope(conn, env):
                        inserted += 1
                    else:
                        duplicates += 1
                await conn.commit()
            # Deliberately NOT calling _evict_old_buckets here: a bulk
            # historical import writes to old buckets by design, and eviction
            # would seal them (chmod 0o400) mid-import, breaking subsequent
            # writes. import_envelopes does NOT seal/unseal buckets.
        return {"inserted": inserted, "duplicates": duplicates, "buckets": buckets}

    async def search_text(
        self,
        query: str,
        *,
        since: float | None = None,
        until: float | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """FTS5 text search across this agent's buckets in the time range.

        ``query`` is a plain-text string (not FTS5 syntax). Terms are split on
        whitespace, each term is phrase-matched, and all terms are ANDed.
        Hits are joined back to trace_events rows and returned newest-first,
        capped at ``limit`` (max 1000)."""
        fts_query = _fts_phrase_query(query)
        if not fts_query:
            return []
        limit = max(1, min(limit, 1000))
        trace_dir = _agent_trace_dir(self._data_dir, self._slug)
        if not trace_dir.exists():
            return []
        db_files = sorted(trace_dir.glob("*.db"), reverse=True)
        out: list[dict] = []
        seen_ids: set[str] = set()
        async with self._lock:
            for db_file in db_files:
                bucket = db_file.stem
                if not _bucket_overlaps(bucket, since, until):
                    continue
                try:
                    conn = await aiosqlite.connect(
                        f"file:{_bucket_db_path(self._data_dir, self._slug, bucket)}?mode=ro",
                        uri=True,
                    )
                    try:
                        cur = await conn.execute(
                            """SELECT t.id, t.created_at, t.kind, t.payload
                               FROM trace_events_fts f
                               JOIN trace_events t ON t.id = f.id
                               WHERE trace_events_fts MATCH ?
                               ORDER BY t.created_at DESC""",
                            (fts_query,),
                        )
                        rows = await cur.fetchall()
                        cols = [d[0] for d in cur.description]
                        await cur.close()
                        for r in rows:
                            ev = dict(zip(cols, r))
                            try:
                                ev["payload"] = json.loads(
                                    ev.get("payload") or "{}"
                                )
                            except Exception as exc:
                                logger.debug(
                                    "trace_store: search_text payload parse failed for %s: %s",
                                    ev.get("id"), exc,
                                )
                            ev_id = ev.get("id")
                            if ev_id and ev_id in seen_ids:
                                continue
                            if ev_id:
                                seen_ids.add(ev_id)
                            out.append(ev)
                    finally:
                        await conn.close()
                except Exception as exc:
                    logger.warning(
                        "trace_store: search_text bucket %s skipped: %s",
                        bucket, exc,
                    )
                if len(out) >= limit * 4:
                    break
        out.sort(key=lambda e: e.get("created_at") or 0, reverse=True)
        return out[:limit]

    @staticmethod
    def _validate_envelope(envelope: dict) -> None:
        """Validate a pre-built envelope matches the v1 contract.

        Same rules record() enforces: kind in VALID_KINDS and the v1 key set
        present. Raises ValueError on the first violation."""
        if not isinstance(envelope, dict):
            raise TypeError("envelope must be a dict")
        missing = set(ENVELOPE_V1_SCHEMA["envelope"]) - set(envelope.keys())
        if missing:
            raise ValueError(
                f"envelope missing v1 keys: {sorted(missing)}"
            )
        kind = envelope.get("kind")
        if kind not in VALID_KINDS:
            raise ValueError(
                f"unknown kind: {kind!r}; valid: {sorted(VALID_KINDS)}"
            )
        if envelope.get("v") != SCHEMA_VERSION:
            raise ValueError(
                f"envelope v must be {SCHEMA_VERSION}, got {envelope.get('v')!r}"
            )

    def _seal_bucket(self, bucket: str) -> None:
        """chmod .db and .jsonl to 0o400 (read-only) for the given bucket.
        Idempotent -- skips files that are already read-only."""
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
                "trace_store: LATE WRITE FAILED for %s/%s: %s -- EVENT LOST",
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
                "trace_store: FALLBACK WRITE FAILED for %s/%s: %s -- EVENT LOST",
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
                    # Open read-only connection for listing, close after use
                    conn = await aiosqlite.connect(f"file:{_bucket_db_path(self._data_dir, self._slug, bucket)}?mode=ro", uri=True)
                    try:
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
                    finally:
                        await conn.close()
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
        return True  # Non-standard name -- don't skip, let the normal filter reject.
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
