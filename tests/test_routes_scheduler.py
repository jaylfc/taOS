"""Endpoint tests for tinyagentos/routes/scheduler.py."""

from __future__ import annotations

import pytest

from tinyagentos.scheduler.backend_catalog import BackendEntry
from tinyagentos.scheduler.resource import Resource, Tier
from tinyagentos.scheduler.scheduler import Scheduler
from tinyagentos.scheduler.types import (
    Capability,
    ResourceRef,
    ResourceSignature,
    Task,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler() -> Scheduler:
    """A Scheduler with a single registered CPU resource, no external deps."""
    scheduler = Scheduler()
    scheduler.register(
        Resource(
            name="cpu-inference",
            signature=ResourceSignature(platform="cpu-x86_64", runtime="native"),
            concurrency=1,
            tier=Tier.CPU,
            potential_capabilities={"llm-chat"},
            get_capabilities=lambda: {"llm-chat"},
            backend_lookup=lambda capability: "http://localhost:8080",
        )
    )
    return scheduler


class _FakeCatalog:
    """Minimal BackendCatalog stand-in exposing backends() + all_models()."""

    def __init__(self, entries, models):
        self._entries = entries
        self._models = models

    def backends(self):
        return list(self._entries)

    def all_models(self, capability=None):
        return list(self._models)


# ---------------------------------------------------------------------------
# /api/scheduler/stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_when_not_initialised_returns_503(client):
    resp = await client.get("/api/scheduler/stats")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "resource scheduler not initialised"


@pytest.mark.asyncio
async def test_stats_returns_live_shape(client, app):
    app.state.resource_scheduler = _make_scheduler()

    resp = await client.get("/api/scheduler/stats")
    assert resp.status_code == 200

    data = resp.json()
    for key in (
        "submitted",
        "completed",
        "errors",
        "rejected",
        "active",
        "resources",
    ):
        assert key in data, f"missing stats key: {key}"
    assert data["submitted"] == 0
    assert data["completed"] == 0
    assert data["errors"] == 0
    assert data["rejected"] == 0
    assert data["active"] == 0

    resources = data["resources"]
    assert isinstance(resources, list)
    assert len(resources) == 1
    entry = resources[0]
    for key in (
        "name",
        "platform",
        "runtime",
        "runtime_version",
        "concurrency",
        "in_flight",
        "tier",
        "capabilities",
        "potential_capabilities",
    ):
        assert key in entry, f"missing resource key: {key}"
    assert entry["name"] == "cpu-inference"
    assert entry["concurrency"] == 1


# ---------------------------------------------------------------------------
# /api/scheduler/tasks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tasks_when_not_initialised_returns_503(client):
    resp = await client.get("/api/scheduler/tasks")
    assert resp.status_code == 503
    assert resp.json()["error"] == "resource scheduler not initialised"


@pytest.mark.asyncio
async def test_tasks_limit_validation_returns_422(client, app):
    app.state.resource_scheduler = _make_scheduler()

    resp = await client.get("/api/scheduler/tasks?limit=abc")
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)


@pytest.mark.asyncio
async def test_tasks_returns_history(client, app):
    scheduler = _make_scheduler()

    async def _payload(resource):
        return {"ok": True}

    task = Task(
        capability=Capability.LLM_CHAT,
        payload=_payload,
        preferred_resources=[ResourceRef(name="cpu-inference")],
        submitter="test",
    )
    await scheduler.submit(task)
    app.state.resource_scheduler = scheduler

    resp = await client.get("/api/scheduler/tasks")
    assert resp.status_code == 200

    data = resp.json()
    assert isinstance(data["tasks"], list)
    assert len(data["tasks"]) == 1

    record = data["tasks"][0]
    for key in (
        "task_id",
        "capability",
        "submitter",
        "priority",
        "resource",
        "status",
        "submitted_at",
        "completed_at",
        "elapsed_seconds",
        "error",
    ):
        assert key in record, f"missing task record key: {key}"
    assert record["task_id"]
    assert record["capability"] == "llm-chat"
    assert record["submitter"] == "test"
    assert record["resource"] == "cpu-inference"
    assert record["status"] == TaskStatus.COMPLETE.value
    assert isinstance(record["elapsed_seconds"], (int, float))
    assert isinstance(record["completed_at"], (int, float))


# ---------------------------------------------------------------------------
# /api/scheduler/backends
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backends_when_not_initialised_returns_503(client):
    resp = await client.get("/api/scheduler/backends")
    assert resp.status_code == 503
    assert resp.json()["error"] == "backend catalog not initialised"


@pytest.mark.asyncio
async def test_backends_returns_catalog(client, app):
    entry = BackendEntry(
        name="test-backend",
        type="rkllama",
        url="http://localhost:8080",
        status="ok",
        capabilities={"llm-chat", "embedding"},
        models=[{"id": "qwen3", "name": "qwen3"}],
        priority=1,
    )
    catalog = _FakeCatalog(
        entries=[entry],
        models=[
            {
                "id": "qwen3",
                "name": "qwen3",
                "backend": "test-backend",
                "backend_type": "rkllama",
            }
        ],
    )
    app.state.backend_catalog = catalog

    resp = await client.get("/api/scheduler/backends")
    assert resp.status_code == 200

    data = resp.json()
    assert isinstance(data["backends"], list)
    assert len(data["backends"]) == 1
    be = data["backends"][0]
    for key in (
        "name",
        "type",
        "url",
        "status",
        "capabilities",
        "models",
        "priority",
        "last_healthy",
        "last_probed",
        "error",
        "lifecycle_state",
        "auto_manage",
        "keep_alive_minutes",
        "enabled",
    ):
        assert key in be, f"missing backend key: {key}"
    assert be["name"] == "test-backend"
    assert be["type"] == "rkllama"
    assert be["url"] == "http://localhost:8080"
    assert be["status"] == "ok"
    assert "llm-chat" in be["capabilities"]
    assert "embedding" in be["capabilities"]

    assert isinstance(data["models"], list)
    assert data["models"][0]["id"] == "qwen3"
