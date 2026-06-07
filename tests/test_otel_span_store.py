"""Tests for Phase 1 observability additions.

Covers:
  - _build_envelope: ts_start -> duration_ms derivation
  - _build_envelope: explicit duration_ms unchanged by ts_start
  - reasoning_audit kind validates
  - SpanStore: write -> query round-trip (fresh DB)
  - SpanStore: filter by trace_id
  - SpanStore: filter by time window (since/until)
  - GET /api/agents/{name}/otel-spans route
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from tinyagentos.trace_store import (
    _build_envelope,
    VALID_KINDS,
    AgentTraceStore,
)
from tinyagentos.otel.span_store import SpanStore, SpanStoreRegistry


# ---------------------------------------------------------------------------
# 1. _build_envelope: ts_start -> duration_ms
# ---------------------------------------------------------------------------

def test_build_envelope_ts_start_computes_duration_ms():
    """When ts_start is passed and duration_ms is absent, duration_ms is derived."""
    ts_start = time.time() - 0.5  # 500 ms ago
    env = _build_envelope("agent-a", "lifecycle", {"ts_start": ts_start, "payload": {"event": "test"}})
    assert env["duration_ms"] is not None
    assert isinstance(env["duration_ms"], int)
    assert env["duration_ms"] >= 0
    # Rough bound: should not exceed 10 seconds for a half-second-ago start
    assert env["duration_ms"] < 10_000


def test_build_envelope_explicit_duration_ms_not_overwritten():
    """An explicit duration_ms must survive even when ts_start is also provided."""
    ts_start = time.time() - 1.0
    env = _build_envelope(
        "agent-b", "llm_call",
        {
            "ts_start": ts_start,
            "duration_ms": 42,
            "payload": {"status": "success", "messages": [], "response": "ok", "metadata": {}},
        },
    )
    assert env["duration_ms"] == 42


def test_build_envelope_no_ts_start_no_duration_ms_stays_none():
    """Without ts_start or duration_ms, duration_ms remains None."""
    env = _build_envelope("agent-c", "lifecycle", {"payload": {"event": "x"}})
    assert env["duration_ms"] is None


def test_build_envelope_ts_start_not_in_payload():
    """ts_start must not leak into the stored payload."""
    ts_start = time.time() - 0.1
    env = _build_envelope("agent-d", "lifecycle", {"ts_start": ts_start, "payload": {"event": "y"}})
    # The payload dict must not contain ts_start
    assert "ts_start" not in env["payload"]
    # And the envelope dict itself has no ts_start column
    assert "ts_start" not in env


# ---------------------------------------------------------------------------
# 2. reasoning_audit kind
# ---------------------------------------------------------------------------

def test_reasoning_audit_in_valid_kinds():
    assert "reasoning_audit" in VALID_KINDS


@pytest.mark.asyncio
async def test_reasoning_audit_records_without_error(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-audit")
    env = await store.record(
        "reasoning_audit",
        payload={"verdict": "pass", "flags": [], "model": "test-model", "latency_ms": 123},
    )
    assert env["kind"] == "reasoning_audit"
    assert env["payload"]["verdict"] == "pass"
    await store.close()


# ---------------------------------------------------------------------------
# 3. SpanStore write -> query round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_span_store_write_and_query(tmp_path):
    store = SpanStore(tmp_path, "agent-span-1")
    try:
        await store.write_span(
            span_id="span-001",
            trace_id="trace-abc",
            parent_span_id=None,
            name="chat",
            start_time_ns=1_000_000_000,
            end_time_ns=2_000_000_000,
            attributes={"gen_ai.system": "openai"},
            status_code="OK",
            agent_name="agent-span-1",
            conversation_id="conv-xyz",
        )
        spans = await store.query_spans("agent-span-1")
        assert len(spans) == 1
        s = spans[0]
        assert s["span_id"] == "span-001"
        assert s["trace_id"] == "trace-abc"
        assert s["name"] == "chat"
        assert s["attributes"]["gen_ai.system"] == "openai"
        assert s["status_code"] == "OK"
        assert s["conversation_id"] == "conv-xyz"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_span_store_idempotent_write(tmp_path):
    """INSERT OR IGNORE: writing the same span_id twice keeps only one row."""
    store = SpanStore(tmp_path, "agent-span-idem")
    try:
        for _ in range(2):
            await store.write_span(
                span_id="span-dupe",
                trace_id="trace-dupe",
                parent_span_id=None,
                name="execute_tool",
                start_time_ns=500,
                end_time_ns=1500,
                agent_name="agent-span-idem",
            )
        spans = await store.query_spans("agent-span-idem")
        assert len(spans) == 1
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# 4. SpanStore: filter by trace_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_span_store_filter_by_trace_id(tmp_path):
    store = SpanStore(tmp_path, "agent-span-filter")
    now = time.time()
    try:
        await store.write_span(
            span_id="s-tr1", trace_id="tr-1", parent_span_id=None,
            name="chat", start_time_ns=100, end_time_ns=200,
            agent_name="agent-span-filter", created_at=now,
        )
        await store.write_span(
            span_id="s-tr2", trace_id="tr-2", parent_span_id=None,
            name="chat", start_time_ns=300, end_time_ns=400,
            agent_name="agent-span-filter", created_at=now + 1,
        )
        result = await store.query_spans("agent-span-filter", trace_id="tr-1")
        assert len(result) == 1
        assert result[0]["span_id"] == "s-tr1"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# 5. SpanStore: filter by time window
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_span_store_filter_by_time_window(tmp_path):
    store = SpanStore(tmp_path, "agent-span-time")
    base = time.time()
    try:
        for i in range(5):
            await store.write_span(
                span_id=f"span-t{i}",
                trace_id="tr-time",
                parent_span_id=None,
                name="chat",
                start_time_ns=i * 1_000_000,
                end_time_ns=(i + 1) * 1_000_000,
                agent_name="agent-span-time",
                created_at=base + i,
            )
        # Query only spans at t=base+1, base+2, base+3 (i=1,2,3)
        result = await store.query_spans(
            "agent-span-time",
            since=base + 0.5,
            until=base + 3.5,
        )
        assert len(result) == 3
        span_ids = {s["span_id"] for s in result}
        assert span_ids == {"span-t1", "span-t2", "span-t3"}
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# 6. SpanStore: limit honoured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_span_store_limit(tmp_path):
    store = SpanStore(tmp_path, "agent-span-lim")
    now = time.time()
    try:
        for i in range(20):
            await store.write_span(
                span_id=f"span-lim-{i}",
                trace_id="tr-lim",
                parent_span_id=None,
                name="chat",
                start_time_ns=i,
                end_time_ns=i + 1,
                agent_name="agent-span-lim",
                created_at=now + i,
            )
        result = await store.query_spans("agent-span-lim", limit=5)
        assert len(result) == 5
        # Newest-first
        assert result[0]["created_at"] > result[-1]["created_at"]
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# 7. SpanStoreRegistry caches instances
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_span_store_registry_same_instance(tmp_path):
    registry = SpanStoreRegistry(tmp_path)
    s1 = await registry.get("some-agent")
    s2 = await registry.get("some-agent")
    assert s1 is s2
    await registry.close_all()


# ---------------------------------------------------------------------------
# 8. GET /api/agents/{name}/otel-spans route
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_otel_spans_route_no_registry(client):
    """Without span_store_registry on app.state, returns empty spans list."""
    app = client._transport.app
    # Ensure no registry is set
    original = getattr(app.state, "span_store_registry", None)
    if hasattr(app.state, "span_store_registry"):
        del app.state.span_store_registry
    try:
        resp = await client.get("/api/agents/my-agent/otel-spans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_name"] == "my-agent"
        assert body["spans"] == []
    finally:
        if original is not None:
            app.state.span_store_registry = original


@pytest.mark.asyncio
async def test_otel_spans_route_returns_spans(client, tmp_path):
    """With a registry, spans written to the store are returned by the route."""
    app = client._transport.app
    registry = SpanStoreRegistry(tmp_path)
    original = getattr(app.state, "span_store_registry", None)
    app.state.span_store_registry = registry
    try:
        store = await registry.get("route-agent")
        await store.write_span(
            span_id="route-span-1",
            trace_id="route-trace-1",
            parent_span_id=None,
            name="chat",
            start_time_ns=100_000_000,
            end_time_ns=200_000_000,
            agent_name="route-agent",
        )

        resp = await client.get("/api/agents/route-agent/otel-spans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_name"] == "route-agent"
        assert len(body["spans"]) == 1
        assert body["spans"][0]["span_id"] == "route-span-1"
    finally:
        await registry.close_all()
        if original is not None:
            app.state.span_store_registry = original
        elif hasattr(app.state, "span_store_registry"):
            del app.state.span_store_registry


@pytest.mark.asyncio
async def test_otel_spans_route_filter_by_trace_id(client, tmp_path):
    """trace_id query param filters correctly."""
    app = client._transport.app
    registry = SpanStoreRegistry(tmp_path)
    original = getattr(app.state, "span_store_registry", None)
    app.state.span_store_registry = registry
    try:
        store = await registry.get("filter-route-agent")
        now = time.time()
        for i, tid in enumerate(["tr-A", "tr-B", "tr-A"]):
            await store.write_span(
                span_id=f"filt-span-{i}",
                trace_id=tid,
                parent_span_id=None,
                name="chat",
                start_time_ns=i,
                end_time_ns=i + 1,
                agent_name="filter-route-agent",
                created_at=now + i,
            )

        resp = await client.get(
            "/api/agents/filter-route-agent/otel-spans",
            params={"trace_id": "tr-A"},
        )
        assert resp.status_code == 200
        spans = resp.json()["spans"]
        assert len(spans) == 2
        assert all(s["trace_id"] == "tr-A" for s in spans)
    finally:
        await registry.close_all()
        if original is not None:
            app.state.span_store_registry = original
        elif hasattr(app.state, "span_store_registry"):
            del app.state.span_store_registry
