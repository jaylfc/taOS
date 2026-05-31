"""Tests for the Model Activity feed route."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.routes.activity import ActivityBuffer


class TestActivityBuffer:
    """Unit tests for the ring buffer without FastAPI."""

    def test_publish_adds_to_buffer(self):
        buf = ActivityBuffer(maxlen=10)
        buf.publish({"type": "model_load", "model_id": "gemma-4", "worker": "gpu-cuda-0"})
        assert len(buf._buffer) == 1
        assert buf._buffer[0]["type"] == "model_load"

    def test_publish_fills_defaults(self):
        buf = ActivityBuffer()
        buf.publish({"type": "request_start"})
        ev = buf._buffer[0]
        assert ev["model_id"] == ""
        assert ev["worker"] == ""
        assert ev["duration_ms"] == 0
        assert ev["tokens_per_sec"] == 0.0
        assert "timestamp" in ev

    def test_publish_rejects_invalid_type(self):
        buf = ActivityBuffer()
        buf.publish({"type": "nonexistent_type"})
        assert len(buf._buffer) == 0

    def test_snapshot_returns_newest_first(self):
        buf = ActivityBuffer()
        buf.publish({"type": "model_load", "model_id": "first"})
        buf.publish({"type": "model_unload", "model_id": "second"})
        snap = buf.snapshot()
        assert snap[0]["model_id"] == "second"
        assert snap[1]["model_id"] == "first"

    def test_ring_buffer_eviction(self):
        buf = ActivityBuffer(maxlen=3)
        for i in range(5):
            buf.publish({"type": "model_load", "model_id": str(i)})
        assert len(buf._buffer) == 3
        # Oldest should be evicted — only 2, 3, 4 remain
        ids = {e["model_id"] for e in buf._buffer}
        assert ids == {"2", "3", "4"}

    @pytest.mark.asyncio
    async def test_subscribe_receives_events(self):
        buf = ActivityBuffer(maxlen=5)
        q = await buf.subscribe()
        buf.publish({"type": "model_load", "model_id": "test"})
        ev = await asyncio.wait_for(q.get(), timeout=1)
        assert ev["model_id"] == "test"
        await buf.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        buf = ActivityBuffer()
        q = await buf.subscribe()
        await buf.unsubscribe(q)
        buf.publish({"type": "model_load", "model_id": "orphan"})
        # Queue should be empty since we unsubscribed
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        buf = ActivityBuffer()
        q1 = await buf.subscribe()
        q2 = await buf.subscribe()
        buf.publish({"type": "request_start", "model_id": "dual"})
        ev1 = await asyncio.wait_for(q1.get(), timeout=1)
        ev2 = await asyncio.wait_for(q2.get(), timeout=1)
        assert ev1["model_id"] == "dual"
        assert ev2["model_id"] == "dual"
        await buf.unsubscribe(q1)
        await buf.unsubscribe(q2)


class TestActivityRoutes:
    """Integration tests against the FastAPI app."""

    @pytest.mark.asyncio
    async def test_post_event_accepted(self, client: AsyncClient):
        resp = await client.post("/api/activity/events", json={
            "type": "model_load", "model_id": "gemma-4", "worker": "gpu-0",
        })
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_post_event_rejects_missing_type(self, client: AsyncClient):
        resp = await client.post("/api/activity/events", json={"model_id": "x"})
        assert resp.status_code == 400
        assert "missing" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_post_event_rejects_invalid_type(self, client: AsyncClient):
        resp = await client.post("/api/activity/events", json={
            "type": "bogus_event",
        })
        assert resp.status_code == 400
        assert "unknown" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_post_event_rejects_non_json(self, client: AsyncClient):
        resp = await client.post(
            "/api/activity/events",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_history_returns_events(self, client: AsyncClient):
        # Publish unique events so we can identify them
        tag = "hist-test-1"
        for i in range(3):
            await client.post("/api/activity/events", json={
                "type": "model_load", "model_id": f"{tag}-{i}", "worker": tag,
            })
        resp = await client.get("/api/activity/history")
        assert resp.status_code == 200
        data = resp.json()
        # At least our 3 events should be present (buffer may have prior events)
        our_events = [e for e in data["events"] if e.get("worker") == tag]
        assert len(our_events) == 3
        # Newest first
        assert our_events[0]["model_id"] == f"{tag}-2"

    @pytest.mark.asyncio
    async def test_history_respects_limit(self, client: AsyncClient):
        for i in range(10):
            await client.post("/api/activity/events", json={
                "type": "request_start", "model_id": f"limit-test-{i}",
            })
        resp = await client.get("/api/activity/history?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 3

    @pytest.mark.asyncio
    async def test_history_filters(self, client: AsyncClient):
        tag = "filter-test"
        await client.post("/api/activity/events", json={
            "type": "model_load", "model_id": f"{tag}-gemma", "worker": f"{tag}-gpu0",
        })
        await client.post("/api/activity/events", json={
            "type": "model_unload", "model_id": f"{tag}-qwen", "worker": f"{tag}-gpu1",
        })
        # Filter by type — all returned events should match
        resp = await client.get(f"/api/activity/history?type=model_load")
        events = resp.json()["events"]
        assert all(e["type"] == "model_load" for e in events)
        assert any(e["model_id"] == f"{tag}-gemma" for e in events)

        # Filter by worker
        resp = await client.get(f"/api/activity/history?worker={tag}-gpu1")
        events = resp.json()["events"]
        assert all(e["worker"] == f"{tag}-gpu1" for e in events)
        assert any(e["model_id"] == f"{tag}-qwen" for e in events)

        # Filter by model
        resp = await client.get(f"/api/activity/history?model={tag}-qwen")
        events = resp.json()["events"]
        assert all(e["model_id"] == f"{tag}-qwen" for e in events)

    @pytest.mark.asyncio
    async def test_sse_stream_replays_history(self, client: AsyncClient):
        tag = "sse-replay"
        await client.post("/api/activity/events", json={
            "type": "model_load", "model_id": tag,
        })
        # Open SSE stream with a short timeout, read first data lines
        async with client.stream("GET", "/api/activity/stream", timeout=5.0) as resp:
            assert resp.status_code == 200
            found = False
            line_count = 0
            try:
                async for line in resp.aiter_lines():
                    line_count += 1
                    if line.startswith("data: "):
                        if tag in line:
                            found = True
                            break
                    if line_count > 500:
                        break  # safety valve
            except Exception:
                pass
            assert found, f"Expected SSE replay to contain {tag}"

    @pytest.mark.asyncio
    async def test_sse_filters_applied(self, client: AsyncClient):
        tag = "sse-filter"
        await client.post("/api/activity/events", json={
            "type": "model_load", "model_id": tag,
        })
        # Open filtered stream with timeout
        async with client.stream(
            "GET", "/api/activity/stream?type=model_unload", timeout=5.0,
        ) as resp:
            assert resp.status_code == 200
            found_model_load = False
            line_count = 0
            try:
                async for line in resp.aiter_lines():
                    line_count += 1
                    if line.startswith("data: "):
                        if tag in line:
                            found_model_load = True
                            break
                    if line_count > 500:
                        break  # safety valve
            except Exception:
                pass
            assert not found_model_load, f"Filtered SSE should not replay {tag}"

    @pytest.mark.asyncio
    async def test_activity_page_returns_html(self, client: AsyncClient):
        resp = await client.get("/api/activity")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert "Model Activity" in body
        assert "timeline" in body
        assert "EventSource" in body or "SSE" in body

    @pytest.mark.asyncio
    async def test_activity_page_has_aria_labels(self, client: AsyncClient):
        resp = await client.get("/api/activity")
        assert resp.status_code == 200
        assert 'aria-label="Filter by event type"' in resp.text
        assert 'aria-label="Activity timeline"' in resp.text
        assert 'aria-live="polite"' in resp.text

    @pytest.mark.asyncio
    async def test_all_event_types_accepted(self, client: AsyncClient):
        for ev_type in (
            "model_load", "model_unload", "model_eviction",
            "route_change", "request_start", "request_finish",
        ):
            resp = await client.post("/api/activity/events", json={
                "type": ev_type,
                "model_id": "test",
                "timestamp": 1234567890.0,
            })
            assert resp.status_code == 200, f"Failed for {ev_type}"

    @pytest.mark.asyncio
    async def test_event_with_all_fields(self, client: AsyncClient):
        resp = await client.post("/api/activity/events", json={
            "type": "request_finish",
            "model_id": "qwen3-30b",
            "worker": "gpu-cuda-0",
            "timestamp": 1717000000.0,
            "duration_ms": 3421,
            "tokens_per_sec": 45.2,
        })
        assert resp.status_code == 200
        # Verify via history
        hist = await client.get("/api/activity/history?limit=1")
        ev = hist.json()["events"][0]
        assert ev["model_id"] == "qwen3-30b"
        assert ev["worker"] == "gpu-cuda-0"
        assert ev["duration_ms"] == 3421
        assert ev["tokens_per_sec"] == 45.2


class TestModuleLevelPublish:
    """Tests for the module-level publish_event function."""

    def test_publish_event_uses_global_buffer(self):
        from tinyagentos.routes import activity
        # Reset any cached buffer first
        activity._buffer = ActivityBuffer(maxlen=10)
        activity.publish_event({
            "type": "route_change",
            "worker": "router",
        })
        assert len(activity._buffer._buffer) == 1
        assert activity._buffer._buffer[0]["type"] == "route_change"
        # Clean up
        activity._buffer = None
