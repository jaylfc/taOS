"""Tests for tinyagentos.trace_store."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.trace_store import (
    AgentTraceStore,
    TraceStoreRegistry,
    _bucket_key,
    _agent_trace_dir,
    _bucket_db_path,
    _bucket_jsonl_path,
    _bucket_late_jsonl_path,
    VALID_KINDS,
    SCHEMA_VERSION,
    SEAL_AGE_SECONDS,
    TRACE_SCHEMA,
    ENVELOPE_V1_SCHEMA,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(year=2024, month=1, day=15, hour=14, minute=0, second=0) -> float:
    """UTC timestamp for a specific date/time."""
    from datetime import datetime, timezone
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()


def _hour_anchor(ts: float) -> int:
    """Anchor a timestamp to 60s after the start of its UTC hour bucket.
    This ensures all offsets up to +200s stay within the same hour bucket.
    """
    return (int(ts) // 3600) * 3600 + 60


# ---------------------------------------------------------------------------
# 1. Round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_round_trip(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-alpha")
    ts = _ts(hour=10)
    env = await store.record(
        "message_in",
        created_at=ts,
        channel_id="ch-1",
        trace_id="tr-abc",
        payload={"from": "user", "text": "hello"},
    )
    assert env["kind"] == "message_in"
    assert env["agent_name"] == "agent-alpha"
    assert env["channel_id"] == "ch-1"
    assert env["trace_id"] == "tr-abc"
    assert env["payload"]["text"] == "hello"
    assert env["v"] == SCHEMA_VERSION

    events = await store.list()
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "message_in"
    assert ev["channel_id"] == "ch-1"
    assert ev["payload"]["text"] == "hello"
    await store.close()


# ---------------------------------------------------------------------------
# 2. Bucket routing by created_at
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bucket_routing_two_hours(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-beta")
    ts_14 = _ts(hour=14)
    ts_14b = _ts(hour=14, minute=30)
    ts_15 = _ts(hour=15)

    await store.record("lifecycle", created_at=ts_14, payload={"event": "start"})
    await store.record("lifecycle", created_at=ts_14b, payload={"event": "ping"})
    await store.record("lifecycle", created_at=ts_15, payload={"event": "stop"})

    trace_dir = _agent_trace_dir(tmp_path, "agent-beta")
    db_files = sorted(trace_dir.glob("*.db"))
    stems = {f.stem for f in db_files}
    assert "2024-01-15T14" in stems
    assert "2024-01-15T15" in stems
    assert len(db_files) == 2
    await store.close()


# ---------------------------------------------------------------------------
# 3. created_at drives bucket, not wall clock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_old_created_at_lands_in_old_bucket(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-gamma")
    # 2 hours in the past
    old_ts = time.time() - 7200
    old_bucket = _bucket_key(old_ts)
    current_bucket = _bucket_key(time.time())
    assert old_bucket != current_bucket

    await store.record("lifecycle", created_at=old_ts, payload={"event": "old"})

    trace_dir = _agent_trace_dir(tmp_path, "agent-gamma")
    db_files = {f.stem for f in trace_dir.glob("*.db")}
    assert old_bucket in db_files
    assert current_bucket not in db_files
    await store.close()


# ---------------------------------------------------------------------------
# 4. JSONL fallback on SQLite failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jsonl_fallback_on_sqlite_failure(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-delta")
    ts = _ts(hour=12)

    with patch("aiosqlite.connect", side_effect=Exception("disk full")):
        env = await store.record(
            "error",
            created_at=ts,
            payload={"stage": "test", "message": "boom"},
        )

    bucket = _bucket_key(ts)
    db_path = _bucket_db_path(tmp_path, "agent-delta", bucket)
    jsonl_path = _bucket_jsonl_path(tmp_path, "agent-delta", bucket)

    assert not db_path.exists()
    assert jsonl_path.exists()

    lines = [json.loads(l) for l in jsonl_path.read_text().strip().splitlines()]
    assert len(lines) == 1
    assert lines[0]["kind"] == "error"
    assert lines[0]["payload"]["stage"] == "test"
    await store.close()


# ---------------------------------------------------------------------------
# 5. list() merges DB + JSONL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_merges_db_and_jsonl(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-epsilon")
    ts_db = _ts(hour=9)
    ts_jl = _ts(hour=9, minute=30)

    # Write one event normally (goes to DB)
    await store.record("message_in", created_at=ts_db, payload={"from": "user", "text": "db event"})

    # Simulate a fallback JSONL entry for the same bucket
    bucket = _bucket_key(ts_jl)
    jl_path = _bucket_jsonl_path(tmp_path, "agent-epsilon", bucket)
    jl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jl_path, "a") as f:
        f.write(json.dumps({
            "v": 1, "id": "aabbcc", "trace_id": None, "parent_id": None,
            "created_at": ts_jl, "agent_name": "agent-epsilon",
            "kind": "message_out", "channel_id": None, "thread_id": None,
            "backend_name": None, "model": None, "duration_ms": None,
            "tokens_in": None, "tokens_out": None, "cost_usd": None,
            "error": None, "payload": {"content": "jsonl event"},
        }) + "\n")

    events = await store.list()
    kinds = {e["kind"] for e in events}
    assert "message_in" in kinds
    assert "message_out" in kinds
    await store.close()


# ---------------------------------------------------------------------------
# 6. list() filter by kind / channel_id / trace_id / since / until
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_filters(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-zeta")
    ts_base = _ts(hour=8)

    await store.record("message_in", created_at=ts_base, channel_id="ch-A", trace_id="tr-1", payload={"from": "u", "text": "a"})
    await store.record("message_out", created_at=ts_base + 1, channel_id="ch-A", trace_id="tr-1", payload={"content": "b"})
    await store.record("llm_call", created_at=ts_base + 2, channel_id="ch-B", trace_id="tr-2",
                       payload={"status": "success", "messages": [], "response": "ok", "metadata": {}})

    # filter by kind
    result = await store.list(kind="message_in")
    assert all(e["kind"] == "message_in" for e in result)
    assert len(result) == 1

    # filter by channel_id
    result = await store.list(channel_id="ch-B")
    assert len(result) == 1
    assert result[0]["kind"] == "llm_call"

    # filter by trace_id
    result = await store.list(trace_id="tr-1")
    assert len(result) == 2

    # since / until
    result = await store.list(since=ts_base + 1.5, until=ts_base + 2.5)
    assert len(result) == 1
    assert result[0]["kind"] == "llm_call"

    await store.close()


# ---------------------------------------------------------------------------
# 7. list() limit honoured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_limit(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-eta")
    ts_base = _ts(hour=7)
    for i in range(20):
        await store.record("lifecycle", created_at=ts_base + i, payload={"event": f"e{i}"})

    result = await store.list(limit=5)
    assert len(result) == 5
    # newest first
    assert result[0]["created_at"] > result[-1]["created_at"]
    await store.close()


# ---------------------------------------------------------------------------
# 8. Connection eviction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connection_eviction(tmp_path):
    """Eviction keeps current bucket + its immediate predecessor; older
    buckets are closed. We mock time.time() so the eviction wall-clock
    matches the event timestamps."""
    store = AgentTraceStore(tmp_path, "agent-theta")

    # Anchor: T14 and T16 in a fixed past date.
    ts_14 = _ts(hour=14)
    ts_16 = _ts(hour=16)
    bucket_14 = _bucket_key(ts_14)  # "2024-01-15T14"
    bucket_16 = _bucket_key(ts_16)  # "2024-01-15T16"

    # When writing ts_14, mock wall-clock to ts_14 so eviction keeps T14.
    with patch("tinyagentos.trace_store.time") as mock_time:
        mock_time.time.return_value = ts_14
        await store.record("lifecycle", created_at=ts_14, payload={"event": "a"})
    assert bucket_14 in store._connections

    # When writing ts_16, mock wall-clock to ts_16 so eviction drops T14
    # (keeps T16 and T15 only).
    with patch("tinyagentos.trace_store.time") as mock_time:
        mock_time.time.return_value = ts_16
        await store.record("lifecycle", created_at=ts_16, payload={"event": "b"})

    assert bucket_14 not in store._connections
    assert bucket_16 in store._connections
    await store.close()


# ---------------------------------------------------------------------------
# 9. Unknown kind raises ValueError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_kind_raises(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-iota")
    with pytest.raises(ValueError, match="unknown kind"):
        await store.record("not_a_real_kind", payload={})
    await store.close()


# ---------------------------------------------------------------------------
# 10. forget() closes connections and evicts from registry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forget_closes_and_evicts(tmp_path):
    registry = TraceStoreRegistry(tmp_path)
    store = await registry.get("agent-kappa")
    now = time.time()
    # Write with wall clock matching the event so eviction keeps the bucket.
    with patch("tinyagentos.trace_store.time") as mock_time:
        mock_time.time.return_value = now
        await store.record("lifecycle", created_at=now, payload={"event": "x"})
    assert len(store._connections) > 0

    await registry.forget("agent-kappa")
    assert "agent-kappa" not in registry._stores
    # connections should be cleared
    assert len(store._connections) == 0


# ---------------------------------------------------------------------------
# Extra: registry get returns same store instance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_same_instance(tmp_path):
    registry = TraceStoreRegistry(tmp_path)
    s1 = await registry.get("my-agent")
    s2 = await registry.get("my-agent")
    assert s1 is s2
    await registry.close_all()


# ---------------------------------------------------------------------------
# Extra: list() returns empty for agent with no trace dir
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_empty_no_dir(tmp_path):
    store = AgentTraceStore(tmp_path, "brand-new-agent")
    result = await store.list()
    assert result == []
    await store.close()


# ---------------------------------------------------------------------------
# 11. Seal: chmod 0o400 after 2h eviction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seal_chmod_0400_after_2h(tmp_path):
    """An old bucket (>= SEAL_AGE_SECONDS ago) is chmod'd 0o400 during eviction."""
    store = AgentTraceStore(tmp_path, "agent-seal-a")
    # Write an event timestamped 3 hours ago so it lands in an old bucket.
    old_ts = time.time() - 3 * 3600
    old_bucket = _bucket_key(old_ts)
    current_ts = time.time()
    current_bucket = _bucket_key(current_ts)
    assert old_bucket != current_bucket

    # Write the old event (opens old bucket connection).
    with patch("tinyagentos.trace_store.time") as mock_time:
        mock_time.time.return_value = old_ts
        await store.record("lifecycle", created_at=old_ts, payload={"event": "old"})

    # Write a current event — eviction runs with the real wall-clock which
    # is >= SEAL_AGE_SECONDS away from old_bucket, triggering sealing.
    await store.record("lifecycle", created_at=current_ts, payload={"event": "now"})

    db_path = _bucket_db_path(tmp_path, "agent-seal-a", old_bucket)
    assert db_path.exists(), "old bucket .db should exist"
    mode = stat.S_IMODE(os.stat(str(db_path)).st_mode)
    assert mode == 0o400, f"expected 0o400, got {oct(mode)}"

    await store.close()
    # Restore write permission so tmp_path cleanup works.
    os.chmod(str(db_path), 0o600)


# ---------------------------------------------------------------------------
# 12. Seal: idempotent — already-sealed files are not re-chmod'd
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seal_idempotent(tmp_path):
    """_seal_bucket called twice only chmod's each file once."""
    store = AgentTraceStore(tmp_path, "agent-seal-b")
    old_ts = time.time() - 3 * 3600
    old_bucket = _bucket_key(old_ts)

    with patch("tinyagentos.trace_store.time") as mock_time:
        mock_time.time.return_value = old_ts
        await store.record("lifecycle", created_at=old_ts, payload={"event": "old"})
    await store.close()

    db_path = _bucket_db_path(tmp_path, "agent-seal-b", old_bucket)
    assert db_path.exists()

    chmod_calls: list[str] = []
    real_chmod = os.chmod

    def counting_chmod(path, mode):
        chmod_calls.append(str(path))
        real_chmod(path, mode)

    with patch("os.chmod", side_effect=counting_chmod):
        store._seal_bucket(old_bucket)
        calls_after_first = len(chmod_calls)
        # Second call — file is already read-only, should not chmod again.
        store._seal_bucket(old_bucket)

    assert calls_after_first == 1, "expected exactly one chmod on first seal"
    assert len(chmod_calls) == 1, "second seal should be a no-op"

    # Restore for cleanup.
    os.chmod(str(db_path), 0o600)


# ---------------------------------------------------------------------------
# 13. Late event routes to .late.jsonl when bucket is sealed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_late_event_writes_late_jsonl_not_db(tmp_path):
    """Writing to a sealed bucket creates .late.jsonl and leaves .db unchanged."""
    # Use fixed timestamps so the test is deterministic regardless of when it
    # runs. Previous versions computed old_ts from time.time() at test start,
    # meaning the expected late_path bucket could diverge from the actually
    # written bucket if time.time() moved across an hour boundary between the
    # computation of old_ts and the record() call.
    OLD_TS = 1700000000.0   # 2023-11-14T22:13:20Z — fixed point in the past
    LATE_TS = OLD_TS + 60   # within the same UTC hour bucket

    store = AgentTraceStore(tmp_path, "agent-seal-c")
    old_bucket = _bucket_key(OLD_TS)

    # Populate and close the old bucket so the .db exists.
    await store.record("lifecycle", created_at=OLD_TS, payload={"event": "old"})
    await store.close()
    # aiosqlite's worker thread can return from close() before the underlying
    # SQLite connection FD is fully released; yield long enough for the worker
    # to drain so the chmod below takes effect on every FD across CI runners.
    await asyncio.sleep(0.5)

    db_path = _bucket_db_path(tmp_path, "agent-seal-c", old_bucket)
    late_path = _bucket_late_jsonl_path(tmp_path, "agent-seal-c", old_bucket)

    # Manually seal the bucket.
    os.chmod(str(db_path), 0o400)
    db_mtime_before = db_path.stat().st_mtime

    # Now write another event with the same old bucket timestamp.
    store2 = AgentTraceStore(tmp_path, "agent-seal-c")
    env = await store2.record("lifecycle", created_at=LATE_TS, payload={"event": "late"})
    await store2.close()

    assert late_path.exists(), f".late.jsonl should have been created at {late_path}"
    lines = [json.loads(l) for l in late_path.read_text().strip().splitlines()]
    assert len(lines) == 1
    assert lines[0]["payload"]["event"] == "late"

    # .db must not have been touched.
    assert db_path.stat().st_mtime == db_mtime_before

    # Cleanup.
    os.chmod(str(db_path), 0o600)


# ---------------------------------------------------------------------------
# 14. list() merges .db + .jsonl + .late.jsonl
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_merges_db_jsonl_late_jsonl(tmp_path):
    """list() returns events from all three file types in a bucket."""
    store = AgentTraceStore(tmp_path, "agent-merge-all")
    bucket_ts = _ts(hour=10)
    bucket = _bucket_key(bucket_ts)

    # Write one event to DB.
    env_db = await store.record(
        "lifecycle", created_at=bucket_ts, payload={"event": "db"}
    )
    await store.close()

    trace_dir = _agent_trace_dir(tmp_path, "agent-merge-all")

    # Manually add a JSONL fallback entry.
    jl_path = _bucket_jsonl_path(tmp_path, "agent-merge-all", bucket)
    with open(jl_path, "a") as f:
        f.write(json.dumps({
            "v": 1, "id": "jsonl-id-001", "trace_id": None, "parent_id": None,
            "created_at": bucket_ts + 10, "agent_name": "agent-merge-all",
            "kind": "lifecycle", "channel_id": None, "thread_id": None,
            "backend_name": None, "model": None, "duration_ms": None,
            "tokens_in": None, "tokens_out": None, "cost_usd": None,
            "error": None, "payload": {"event": "jsonl"},
        }) + "\n")

    # Manually add a late.jsonl entry.
    late_path = _bucket_late_jsonl_path(tmp_path, "agent-merge-all", bucket)
    with open(late_path, "a") as f:
        f.write(json.dumps({
            "v": 1, "id": "late-id-001", "trace_id": None, "parent_id": None,
            "created_at": bucket_ts + 20, "agent_name": "agent-merge-all",
            "kind": "lifecycle", "channel_id": None, "thread_id": None,
            "backend_name": None, "model": None, "duration_ms": None,
            "tokens_in": None, "tokens_out": None, "cost_usd": None,
            "error": None, "payload": {"event": "late"},
        }) + "\n")

    store2 = AgentTraceStore(tmp_path, "agent-merge-all")
    events = await store2.list()
    await store2.close()

    event_sources = {e["payload"]["event"] if isinstance(e.get("payload"), dict) else None
                     for e in events}
    assert "db" in event_sources
    assert "jsonl" in event_sources
    assert "late" in event_sources
    assert len(events) == 3

    # Verify sorted newest-first.
    times = [e["created_at"] for e in events]
    assert times == sorted(times, reverse=True)


# ---------------------------------------------------------------------------
# 15. list() dedup by id: primary (.db) wins over .late.jsonl
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_dedup_by_id_primary_wins(tmp_path):
    """When the same id appears in .db and .late.jsonl, only the .db version
    is returned."""
    store = AgentTraceStore(tmp_path, "agent-dedup")
    bucket_ts = _ts(hour=11)
    bucket = _bucket_key(bucket_ts)
    shared_id = "shared-event-id-xyz"

    env_db = await store.record(
        "lifecycle",
        id=shared_id,
        created_at=bucket_ts,
        payload={"source": "db"},
    )
    await store.close()

    # Inject the same id into .late.jsonl with a different payload.
    late_path = _bucket_late_jsonl_path(tmp_path, "agent-dedup", bucket)
    late_path.parent.mkdir(parents=True, exist_ok=True)
    with open(late_path, "a") as f:
        f.write(json.dumps({
            "v": 1, "id": shared_id, "trace_id": None, "parent_id": None,
            "created_at": bucket_ts + 5, "agent_name": "agent-dedup",
            "kind": "lifecycle", "channel_id": None, "thread_id": None,
            "backend_name": None, "model": None, "duration_ms": None,
            "tokens_in": None, "tokens_out": None, "cost_usd": None,
            "error": None, "payload": {"source": "late"},
        }) + "\n")

    store2 = AgentTraceStore(tmp_path, "agent-dedup")
    events = await store2.list()
    await store2.close()

    # Only one event with that id.
    matching = [e for e in events if e.get("id") == shared_id]
    assert len(matching) == 1
    payload = matching[0].get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload.get("source") == "db", "DB version should win over late version"


@pytest.mark.asyncio
async def test_list_does_not_leak_connections_or_threads(tmp_path):
    """list() must not leave open connections or threads behind."""
    import sqlite3
    import threading
    store = AgentTraceStore(tmp_path, "agent-leak")

    for i in range(20):
        ts = _ts(hour=i)
        bucket = _bucket_key(ts)
        db_path = _bucket_db_path(tmp_path, "agent-leak", bucket)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.executescript(TRACE_SCHEMA)
        conn.execute(
            "INSERT INTO trace_events (id, v, created_at, agent_name, kind, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (f"id-{i}", 1, ts, "agent-leak", "lifecycle", '{"event":"test"}'),
        )
        conn.commit()
        conn.close()

    before_threads = threading.active_count()
    before_conns = len(store._connections)

    await store.list()
    after_first_threads = threading.active_count()
    after_first_conns = len(store._connections)

    await store.list()
    after_second_threads = threading.active_count()
    after_second_conns = len(store._connections)

    assert after_second_conns == before_conns, (
        f"open connections leaked: {after_second_conns} (before {before_conns})"
    )
    assert after_second_threads <= before_threads + 1, (
        f"threads leaked: {after_second_threads} (before {before_threads})"
    )

    await store.close()


# ---------------------------------------------------------------------------
# GPU work queue per-op audit record (taOS #1864 Slice A1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_gpu_op_kind_accepted(tmp_path):
    store = AgentTraceStore(tmp_path, "_system_")
    env = await store.record(
        "gpu_op", model="qwen2.5:7b", backend_name="local-ollama",
        duration_ms=1200,
        payload={"op": "load", "outcome": "ok", "wait_ms": 0,
                 "queue_position_at_enqueue": 0, "queue_depth_at_admit": 0,
                 "required_vram_mb": 2048, "free_vram_mb_at_enqueue": 8192,
                 "free_vram_mb_at_admit": 8192, "reserved_vram_mb_at_admit": 0,
                 "resident_models_at_admit": 0,
                 "evictions_triggered": [], "priority": 20,
                 "submitter": "_system_"},
    )
    assert env["kind"] == "gpu_op"
    rows = await store.list(kind="gpu_op")
    assert rows and rows[0]["payload"]["outcome"] == "ok"
    await store.close()


# ---------------------------------------------------------------------------
# 16. _agent_trace_dir rejects path-traversal slugs (S2-9)
# ---------------------------------------------------------------------------

class TestAgentTraceDirPathSafety:
    """_agent_trace_dir must never produce a path outside data_dir."""

    @pytest.mark.parametrize("bad_slug", [
        "../x",
        "a/b",
        "..",
        "",
        "../../etc/passwd",
        "foo\\bar",
        "foo;rm -rf /",
    ])
    def test_agent_trace_dir_rejects_traversal_slugs(self, tmp_path, bad_slug):
        from tinyagentos.trace_store import _agent_trace_dir
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with pytest.raises(ValueError, match="invalid agent slug"):
            _agent_trace_dir(data_dir, bad_slug)

    def test_agent_trace_dir_valid_slug_inside_data_dir(self, tmp_path):
        from tinyagentos.trace_store import _agent_trace_dir
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        result = _agent_trace_dir(data_dir, "my-agent-123")
        assert result.is_relative_to(data_dir)

    def test_agent_trace_dir_empty_slug_raises(self, tmp_path):
        from tinyagentos.trace_store import _agent_trace_dir
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with pytest.raises(ValueError, match="invalid agent slug"):
            _agent_trace_dir(data_dir, "")

    def test_no_path_created_outside_data_dir_for_traversal(self, tmp_path):
        from tinyagentos.trace_store import _agent_trace_dir
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Attempt the traversal - it must raise, not silently write elsewhere.
        with pytest.raises(ValueError):
            _agent_trace_dir(data_dir, "../../escaped-agent")
        # Confirm nothing was written outside data_dir.
        for p in tmp_path.rglob("*"):
            try:
                if p.is_relative_to(data_dir):
                    continue
            except Exception:
                pass
            assert not p.is_file(), f"file outside data_dir: {p}"


# ---------------------------------------------------------------------------
# 17. AgentTraceStore round-trip with valid slug still works (S2-9)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_round_trip_valid_slug_after_safe_slug(tmp_path):
    """A valid slug round-trips cleanly through the store."""
    store = AgentTraceStore(tmp_path, "valid-agent-42")
    ts = _ts(hour=10)
    env = await store.record(
        "message_in",
        created_at=ts,
        channel_id="ch-1",
        trace_id="tr-abc",
        payload={"from": "user", "text": "hello"},
    )
    assert env["agent_name"] == "valid-agent-42"
    events = await store.list()
    assert len(events) == 1
    await store.close()


# ---------------------------------------------------------------------------
# 18. Bulk historical import (T2b) — RED tests for import_envelopes / search_text
# ---------------------------------------------------------------------------

def _envelope(**overrides) -> dict:
    """Build a valid v1 envelope with sane defaults for import tests."""
    base = {
        "v": SCHEMA_VERSION,
        "id": overrides.pop("id", None) or ("env-" + uuid.uuid4().hex[:12]),
        "trace_id": overrides.pop("trace_id", None),
        "parent_id": overrides.pop("parent_id", None),
        "created_at": overrides.pop("created_at", time.time()),
        "agent_name": overrides.pop("agent_name", "importer-agent"),
        "kind": overrides.pop("kind", "tool_result"),
        "channel_id": overrides.pop("channel_id", None),
        "thread_id": overrides.pop("thread_id", None),
        "backend_name": overrides.pop("backend_name", None),
        "model": overrides.pop("model", None),
        "duration_ms": overrides.pop("duration_ms", None),
        "tokens_in": overrides.pop("tokens_in", None),
        "tokens_out": overrides.pop("tokens_out", None),
        "cost_usd": overrides.pop("cost_usd", None),
        "error": overrides.pop("error", None),
        "payload": overrides.pop("payload", {}),
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_import_envelopes_lands_in_bucket_db_not_late_jsonl(tmp_path):
    """RED: import_envelopes routes 30-day-old events into the bucket DB and
    writes no .late.jsonl sidecar (the sealed-bucket record() path would)."""
    store = AgentTraceStore(tmp_path, "importer-agent")
    now = time.time()
    old_ts = _hour_anchor(now - 30 * 86400)
    bucket = _bucket_key(old_ts)
    envelopes = [
        _envelope(id="imp-1", created_at=old_ts, kind="tool_result",
                  payload={"tool": "shell", "result": "alpha-token-xyz"}),
        _envelope(id="imp-2", created_at=old_ts + 5, kind="message_in",
                  payload={"from": "user", "text": "second event"}),
        _envelope(id="imp-3", created_at=old_ts + 10, kind="llm_call",
                  payload={"status": "success", "messages": [], "response": "ok", "metadata": {}}),
    ]
    result = await store.import_envelopes(envelopes)
    assert result["inserted"] == 3
    assert result["duplicates"] == 0
    assert bucket in result["buckets"]

    events = await store.list(since=old_ts, until=old_ts + 20, limit=100)
    ids = {e["id"] for e in events}
    assert {"imp-1", "imp-2", "imp-3"}.issubset(ids), events

    late_path = _bucket_late_jsonl_path(tmp_path, "importer-agent", bucket)
    assert not late_path.exists(), "import must not write a .late.jsonl sidecar"

    db_path = _bucket_db_path(tmp_path, "importer-agent", bucket)
    assert db_path.exists()
    await store.close()


@pytest.mark.asyncio
async def test_import_envelopes_idempotent_duplicates(tmp_path):
    """RED: re-running import_envelopes with the same ids reports duplicates."""
    store = AgentTraceStore(tmp_path, "importer-agent")
    now = time.time()
    old_ts = _hour_anchor(now - 30 * 86400)
    envelopes = [
        _envelope(id=f"dup-{i}", created_at=old_ts + i, kind="tool_result",
                  payload={"tool": "shell", "result": f"r{i}"})
        for i in range(3)
    ]
    first = await store.import_envelopes(envelopes)
    assert first["inserted"] == 3 and first["duplicates"] == 0

    second = await store.import_envelopes(envelopes)
    assert second["inserted"] == 0
    assert second["duplicates"] == 3, second

    events = await store.list(since=old_ts, until=old_ts + 10, limit=100)
    assert len(events) == 3
    await store.close()


@pytest.mark.asyncio
async def test_search_text_finds_unique_token(tmp_path):
    """RED: search_text finds a unique token from a tool_result and not from
    another agent's store."""
    store = AgentTraceStore(tmp_path, "searcher-agent")
    now = time.time()
    old_ts = _hour_anchor(now - 30 * 86400)
    envelopes = [
        _envelope(id="srch-1", created_at=old_ts, kind="tool_result",
                  payload={"tool": "shell", "result": "the-unicorn-secret-42"}),
        _envelope(id="srch-2", created_at=old_ts + 5, kind="message_in",
                  payload={"from": "user", "text": "unrelated chatter"}),
    ]
    await store.import_envelopes(envelopes)

    hits = await store.search_text("unicorn secret", since=old_ts - 10, limit=10)
    assert any(h.get("id") == "srch-1" for h in hits), hits
    assert not any(h.get("id") == "srch-2" for h in hits), hits
    await store.close()

    other = AgentTraceStore(tmp_path, "other-agent")
    other_envs = [
        _envelope(id="oth-1", created_at=old_ts, kind="tool_result",
                  payload={"tool": "shell", "result": "totally different topic"}),
    ]
    await other.import_envelopes(other_envs)
    other_hits = await other.search_text("unicorn secret", since=old_ts - 10, limit=10)
    assert other_hits == [], other_hits
    await other.close()


# ---------------------------------------------------------------------------
# 19. RED tests for tsk-yeyspv fixes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_text_handles_card_ids_and_special_chars(tmp_path):
    """RED: search_text should find card IDs like 'tsk-abc123' and handle
    apostrophes like \"it's\" without raising FTS5 syntax errors."""
    store = AgentTraceStore(tmp_path, "search-fix-agent")
    now = time.time()
    old_ts = _hour_anchor(now - 30 * 86400)
    envelopes = [
        _envelope(id="card-1", created_at=old_ts, kind="tool_result",
                  payload={"tool": "shell", "result": "task completed: tsk-abc123"}),
        _envelope(id="card-2", created_at=old_ts + 5, kind="message_in",
                  payload={"from": "user", "text": "it's working now"}),
    ]
    await store.import_envelopes(envelopes)

    # Card ID search should work (not raise "no such column")
    hits = await store.search_text("tsk-abc123", since=old_ts - 10, limit=10)
    assert any(h.get("id") == "card-1" for h in hits), f"expected card-1 in hits: {hits}"

    # Apostrophe search should work (not raise "fts5: syntax error")
    hits = await store.search_text("it's", since=old_ts - 10, limit=10)
    assert isinstance(hits, list), f"expected list, got {type(hits)}: {hits}"
    # Should find the event with "it's working now"
    assert any(h.get("id") == "card-2" for h in hits), f"expected card-2 in hits: {hits}"

    await store.close()


@pytest.mark.asyncio
async def test_import_envelopes_sealed_bucket_reimport_returns_duplicates(tmp_path):
    """RED: Re-importing into a sealed bucket should return duplicates=3,
    inserted=0 without raising 'readonly database' error."""
    store = AgentTraceStore(tmp_path, "sealed-reimport-agent")
    now = time.time()
    old_ts = _hour_anchor(now - 30 * 86400)
    bucket = _bucket_key(old_ts)
    envelopes = [
        _envelope(id=f"sealed-{i}", created_at=old_ts + i, kind="tool_result",
                  payload={"tool": "shell", "result": f"output-{i}"})
        for i in range(3)
    ]
    # First import
    result1 = await store.import_envelopes(envelopes)
    assert result1["inserted"] == 3
    assert result1["duplicates"] == 0

    # Seal the bucket
    db_path = _bucket_db_path(tmp_path, "sealed-reimport-agent", bucket)
    os.chmod(str(db_path), 0o400)

    try:
        # Re-import same envelopes - should report duplicates, not raise
        result2 = await store.import_envelopes(envelopes)
        assert result2["inserted"] == 0, f"expected inserted=0, got {result2}"
        assert result2["duplicates"] == 3, f"expected duplicates=3, got {result2}"
    finally:
        # Restore for cleanup
        os.chmod(str(db_path), 0o600)

    await store.close()


@pytest.mark.asyncio
async def test_import_envelopes_sealed_bucket_new_envelope_raises_valueerror(tmp_path):
    """RED: Importing a NEW envelope into a sealed bucket should raise ValueError
    before writing to ANY bucket."""
    store = AgentTraceStore(tmp_path, "sealed-new-agent")
    now = time.time()
    old_ts = _hour_anchor(now - 30 * 86400)
    bucket = _bucket_key(old_ts)

    # Import 3 envelopes into old bucket
    old_envelopes = [
        _envelope(id=f"old-{i}", created_at=old_ts + i, kind="tool_result",
                  payload={"tool": "shell", "result": f"old-{i}"})
        for i in range(3)
    ]
    await store.import_envelopes(old_envelopes)

    # Seal the old bucket
    db_path = _bucket_db_path(tmp_path, "sealed-new-agent", bucket)
    os.chmod(str(db_path), 0o400)

    # Also create a current bucket with some data
    current_ts = now
    current_bucket = _bucket_key(current_ts)
    current_envelopes = [
        _envelope(id=f"curr-{i}", created_at=current_ts + i, kind="tool_result",
                  payload={"tool": "shell", "result": f"curr-{i}"})
        for i in range(2)
    ]
    await store.import_envelopes(current_envelopes)

    # Now try to import a mix: existing old envelopes (duplicates) + ONE new envelope for the sealed bucket
    # AND a new envelope for the current bucket - this exercises the no-partial-commit guarantee
    new_sealed_envelope = _envelope(id="new-sealed-1", created_at=old_ts + 100, kind="tool_result",
                                    payload={"tool": "shell", "result": "new-in-sealed"})
    new_current_envelope = _envelope(id="new-curr-1", created_at=current_ts + 10, kind="tool_result",
                                     payload={"tool": "shell", "result": "new-in-current"})
    mixed_envelopes = old_envelopes + [new_sealed_envelope, new_current_envelope]

    try:
        with pytest.raises(ValueError, match="sealed bucket"):
            await store.import_envelopes(mixed_envelopes)
    finally:
        os.chmod(str(db_path), 0o600)

    # Verify the NEW envelope was NOT written to the sealed bucket
    events = await store.list(since=old_ts, until=old_ts + 200, limit=100)
    ids = {e["id"] for e in events}
    assert "new-sealed-1" not in ids, f"new-sealed-1 should not have been written: {ids}"

    # Verify current bucket was not touched either (no partial commit)
    current_events = await store.list(since=current_ts, until=current_ts + 20, limit=100)
    current_ids = {e["id"] for e in current_events}
    assert current_ids == {"curr-0", "curr-1"}, f"current bucket should be unchanged: {current_ids}"

    await store.close()


# ---------------------------------------------------------------------------
# 20. Regression test: sealed-bucket works correctly near hour boundary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_envelopes_sealed_bucket_works_near_hour_boundary(tmp_path, monkeypatch):
    """Regression test: with hour-anchored timestamps, the sealed-bucket check
    works correctly even when the test runs near an hour boundary.
    Pins time.time() to 50s before an hour boundary and verifies ValueError
    is raised for new envelopes targeting a sealed bucket."""
    # Pin wall-clock to 50 seconds before the next UTC hour.
    real_now = time.time()
    pinned_now = (int(real_now) // 3600 + 1) * 3600 - 50

    store = AgentTraceStore(tmp_path, "flake-demo-agent")

    monkeypatch.setattr(time, "time", lambda: pinned_now)

    # Use the FIXED hour-anchored computation (same as the other tests now).
    now = pinned_now
    old_ts = _hour_anchor(now - 30 * 86400)
    bucket = _bucket_key(old_ts)

    # Import old envelopes
    old_envelopes = [
        _envelope(id=f"old-{i}", created_at=old_ts + i, kind="tool_result",
                  payload={"tool": "shell", "result": f"old-{i}"})
        for i in range(3)
    ]
    await store.import_envelopes(old_envelopes)

    # Seal the old bucket
    db_path = _bucket_db_path(tmp_path, "flake-demo-agent", bucket)
    os.chmod(str(db_path), 0o400)

    try:
        # Current bucket (also hour-anchored)
        current_ts = _hour_anchor(now)
        current_bucket = _bucket_key(current_ts)
        current_envelopes = [
            _envelope(id=f"curr-{i}", created_at=current_ts + i, kind="tool_result",
                      payload={"tool": "shell", "result": f"curr-{i}"})
            for i in range(2)
        ]
        await store.import_envelopes(current_envelopes)

        # Try to import a NEW envelope for the sealed bucket
        # With the hour-anchored computation, old_ts + 100 stays in the SAME bucket
        # (since old_ts is anchored to 60s after hour start, +100 = 160s, well within the hour)
        new_sealed_envelope = _envelope(id="new-sealed-1", created_at=old_ts + 100, kind="tool_result",
                                        payload={"tool": "shell", "result": "new-in-sealed"})
        new_current_envelope = _envelope(id="new-curr-1", created_at=current_ts + 10, kind="tool_result",
                                         payload={"tool": "shell", "result": "new-in-current"})
        mixed_envelopes = old_envelopes + [new_sealed_envelope, new_current_envelope]

        # This SHOULD raise ValueError and now it DOES with the fix
        with pytest.raises(ValueError, match="sealed bucket"):
            await store.import_envelopes(mixed_envelopes)
    finally:
        # Restore for cleanup
        os.chmod(str(db_path), 0o600)
        await store.close()
