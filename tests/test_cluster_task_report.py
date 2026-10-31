"""Tests for POST /api/cluster/tasks/report (taOS #3233).

Auth is via _require_lease_access (shared with lease endpoints, tested in
test_leases.py). These tests mock it to focus on the report logic.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.trace_store import TraceStoreRegistry, VALID_KINDS, TraceWriteError


@pytest.mark.asyncio
async def test_task_report_kind_registered():
    assert "task_report" in VALID_KINDS


async def _allow_all(request):
    return None


def _mock_auth():
    return patch(
        "tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all
    )


@pytest.mark.asyncio
async def test_report_task_records_trace(client, app, tmp_data_dir):
    # Set up trace registry manually (lifespan not run in tests)
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    # Use admin auth via local token if available, else patch _require_lease_access
    from unittest.mock import patch
    from fastapi.responses import JSONResponse

    async def allow_all(request):
        return None

    with patch(
        "tinyagentos.routes.cluster._require_lease_access", side_effect=allow_all
    ):
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "lease_id": "l_abc123",
                "caller": "ext-scheduler",
                "model": "llama3:8b",
                "duration_s": 12.5,
                "status": "success",
                "tokens": {"in": 100, "out": 250},
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["worker"] == "w1"
    assert data["resource_id"] == "gpu-cuda-0"
    assert "id" in data

    # Verify it landed in the trace store
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 1
    env = events[0]
    assert env["kind"] == "task_report"
    assert env["model"] == "llama3:8b"
    assert env["duration_ms"] == 12500
    payload = env["payload"]
    assert payload["worker"] == "w1"
    assert payload["resource_id"] == "gpu-cuda-0"
    assert payload["lease_id"] == "l_abc123"
    assert payload["caller"] == "ext-scheduler"
    assert payload["status"] == "success"
    assert payload["tokens"] == {"in": 100, "out": 250}

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_failure_status(client, app, tmp_data_dir):
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    with _mock_auth():
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w2",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "model": "llama3",
                "duration_s": 3.0,
                "status": "failure",
            },
        )
    assert resp.status_code == 200
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 1
    assert events[0]["error"] == "failure"
    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_validation(client, app, tmp_data_dir):
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    with _mock_auth():
        # Invalid status
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "x",
                "duration_s": 1.0,
                "status": "bogus",
            },
        )
        assert resp.status_code == 422

        # Negative duration
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "x",
                "duration_s": -1.0,
                "status": "success",
            },
        )
        assert resp.status_code == 422

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_no_registry(client, app):
    # trace_registry is None -> 503
    app.state.trace_registry = None
    with _mock_auth():
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "x",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_report_task_strict_write_failure_returns_503(client, app, tmp_data_dir):
    """Both sinks fail with strict=True -> 503, no 'recorded' in response."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    store = await app.state.trace_registry.get("taos-cluster-tasks")

    # Force both sinks to fail
    async def _fail_insert(*args, **kwargs):
        raise RuntimeError("sqlite failed")

    def _fail_fallback(*args, **kwargs):
        return False

    with patch.object(store, "_insert_envelope", side_effect=_fail_insert), \
         patch.object(store, "_append_fallback", side_effect=_fail_fallback), \
         patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all):
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 503, resp.text
    data = resp.json()
    assert data["error"] == "trace write failed"
    assert "recorded" not in str(data)

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_worker_hmac_binding(client, app, tmp_data_dir):
    """Worker HMAC caller must report its own name; mismatch -> 403."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    # Mock _identify_principal to return "worker" and set hmac_worker_name on request.state
    async def mock_identify_worker(request):
        request.state.hmac_worker_name = "worker-actual"
        return "worker"

    with patch("tinyagentos.routes.cluster._identify_principal", side_effect=mock_identify_worker), \
         patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all):
        # Mismatch -> 403
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "worker-other",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "worker mismatch"

        # Match -> 200
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "worker-actual",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "recorded"

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_tokens_validation(client, app, tmp_data_dir):
    """tokens dict validation: 17 keys, non-int value, negative value -> 422."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    with patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all):
        # 17 keys -> 422
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {f"k{i}": i for i in range(17)},
            },
        )
        assert resp.status_code == 422, resp.text

        # Non-int value -> 422
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {"in": "not-an-int"},
            },
        )
        assert resp.status_code == 422, resp.text

        # Negative value -> 422
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {"out": -1},
            },
        )
        assert resp.status_code == 422, resp.text

        # Valid tokens (16 keys, int values 0..1e9) -> 200
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {f"k{i}": i for i in range(16)},
            },
        )
        assert resp.status_code == 200, resp.text

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_trace_store_record_non_strict_returns_envelope_on_failure(tmp_data_dir):
    """record() WITHOUT strict still returns envelope when both sinks fail."""
    from tinyagentos.trace_store import AgentTraceStore
    store = AgentTraceStore(tmp_data_dir, "test-agent")

    # Force both sinks to fail
    async def _fail_insert(*args, **kwargs):
        raise RuntimeError("sqlite failed")

    def _fail_fallback(*args, **kwargs):
        return False

    with patch.object(store, "_insert_envelope", side_effect=_fail_insert), \
         patch.object(store, "_append_fallback", side_effect=_fail_fallback):
        # Default strict=False should still return envelope
        env = await store.record("message_in", payload={"from": "user", "text": "hello"})
        assert env["kind"] == "message_in"
        assert env["payload"]["text"] == "hello"
        assert "id" in env
        assert "created_at" in env
