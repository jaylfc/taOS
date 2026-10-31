"""Tests for POST /api/cluster/tasks/report (taOS #3233).

Auth is via _require_lease_access (shared with lease endpoints, tested in
test_leases.py). These tests mock it to focus on the report logic.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tinyagentos.trace_store import TraceStoreRegistry, VALID_KINDS


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
