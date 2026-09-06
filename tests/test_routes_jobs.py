"""Endpoint tests for tinyagentos/routes/jobs.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_request(app):
    req = MagicMock()
    req.app.state = app.state
    return req


@pytest.mark.asyncio
async def test_list_jobs_returns_200(client):
    resp = await client.get("/api/jobs")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_jobs_returns_list(client):
    resp = await client.get("/api/jobs")
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_jobs_empty(client):
    resp = await client.get("/api/jobs")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_jobs_with_status_filter(client):
    resp = await client.get("/api/jobs", params={"status": "pending"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_jobs_with_limit(client):
    resp = await client.get("/api/jobs", params={"limit": 5})
    assert resp.status_code == 200
    assert len(resp.json()) <= 5


@pytest.mark.asyncio
async def test_job_stats_returns_200(client):
    resp = await client.get("/api/jobs/stats")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_job_stats_shape(client):
    data = (await client.get("/api/jobs/stats")).json()
    assert "counts" in data
    assert "running_by_resource" in data
    assert "limits" in data
    assert "total_pending" in data
    assert "total_running" in data
    assert isinstance(data["counts"], dict)
    assert isinstance(data["running_by_resource"], dict)
    assert isinstance(data["limits"], dict)


@pytest.mark.asyncio
async def test_job_stats_empty_queue(client):
    data = (await client.get("/api/jobs/stats")).json()
    assert data["total_pending"] == 0
    assert data["total_running"] == 0


@pytest.mark.asyncio
async def test_running_jobs_returns_200(client):
    resp = await client.get("/api/jobs/running")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_running_jobs_returns_list(client):
    resp = await client.get("/api/jobs/running")
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_running_jobs_empty(client):
    resp = await client.get("/api/jobs/running")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_job_not_found(client):
    resp = await client.get("/api/jobs/nonexistent-id")
    assert resp.status_code == 404
    assert resp.json()["error"] == "Job not found"


@pytest.mark.asyncio
async def test_cancel_job_not_found(client):
    resp = await client.post("/api/jobs/nonexistent-id/cancel")
    assert resp.status_code == 404
    assert resp.json()["error"] == "Job not found or not pending"


@pytest.mark.asyncio
async def test_cleanup_jobs_returns_200(client):
    resp = await client.post("/api/jobs/cleanup")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cleanup_jobs_shape(client):
    data = (await client.post("/api/jobs/cleanup")).json()
    assert "removed" in data
    assert isinstance(data["removed"], int)


@pytest.mark.asyncio
async def test_cleanup_jobs_empty(client):
    data = (await client.post("/api/jobs/cleanup")).json()
    assert data["removed"] == 0


@pytest.mark.asyncio
async def test_list_jobs_after_enqueue(client):
    from tinyagentos.routes.jobs import _get_queue

    app = client._transport.app
    queue = await _get_queue(_make_request(app))
    job_id = await queue.enqueue(job_type="embed", payload={"text": "hello"})

    app.state.job_queue = queue

    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["job_type"] == "embed"
    assert jobs[0]["status"] == "pending"

    await queue.close()
    app.state.job_queue = None


@pytest.mark.asyncio
async def test_get_job_after_enqueue(client):
    from tinyagentos.routes.jobs import _get_queue

    app = client._transport.app
    queue = await _get_queue(_make_request(app))
    job_id = await queue.enqueue(job_type="extract", agent_name="test-agent")

    app.state.job_queue = queue

    resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == job_id
    assert data["job_type"] == "extract"
    assert data["agent_name"] == "test-agent"

    await queue.close()
    app.state.job_queue = None


@pytest.mark.asyncio
async def test_cancel_job_after_enqueue(client):
    from tinyagentos.routes.jobs import _get_queue

    app = client._transport.app
    queue = await _get_queue(_make_request(app))
    job_id = await queue.enqueue(job_type="enrich")

    app.state.job_queue = queue

    resp = await client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["job_id"] == job_id

    await queue.close()
    app.state.job_queue = None


@pytest.mark.asyncio
async def test_cancel_already_cancelled_returns_404(client):
    from tinyagentos.routes.jobs import _get_queue

    app = client._transport.app
    queue = await _get_queue(_make_request(app))
    job_id = await queue.enqueue(job_type="split")

    app.state.job_queue = queue

    await client.post(f"/api/jobs/{job_id}/cancel")
    resp = await client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 404
    assert resp.json()["error"] == "Job not found or not pending"

    await queue.close()
    app.state.job_queue = None


@pytest.mark.asyncio
async def test_stats_after_enqueue(client):
    from tinyagentos.routes.jobs import _get_queue

    app = client._transport.app
    queue = await _get_queue(_make_request(app))
    await queue.enqueue(job_type="embed")
    await queue.enqueue(job_type="extract")

    app.state.job_queue = queue

    resp = await client.get("/api/jobs/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_pending"] == 2
    assert data["counts"]["pending"] == 2

    await queue.close()
    app.state.job_queue = None


@pytest.mark.asyncio
async def test_running_jobs_after_dequeue(client):
    from tinyagentos.routes.jobs import _get_queue

    app = client._transport.app
    queue = await _get_queue(_make_request(app))
    await queue.enqueue(job_type="embed")
    await queue.dequeue()

    app.state.job_queue = queue

    resp = await client.get("/api/jobs/running")
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "running"

    await queue.close()
    app.state.job_queue = None
