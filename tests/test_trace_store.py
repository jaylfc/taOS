"""Tests for tinyagentos.trace_store."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import time
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
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(year=2024, month=1, day=15, hour=14, minute=0, second=0) -> float:
    """UTC timestamp for a specific date/time."""
    from datetime import datetime, timezone
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()


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
