"""Tests for the benchmark routes (tinyagentos/routes/benchmarks.py)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_store(**overrides):
    """Build a mock BenchmarkStore with async methods and optional overrides."""
    store = MagicMock()
    store.has_first_join_run = AsyncMock(
        return_value=overrides.get("has_first_join_run", False)
    )
    store.record = AsyncMock(
        return_value=overrides.get("record_return", 1)
    )
    store.latest_by_worker = AsyncMock(
        return_value=overrides.get("latest_by_worker", [])
    )
    store.history_by_worker = AsyncMock(
        return_value=overrides.get("history_by_worker", [])
    )
    store.leaderboard = AsyncMock(
        return_value=overrides.get("leaderboard", [])
    )
    return store


def _report_payload(**overrides):
    """Build a valid BenchmarkReport JSON body."""
    return {
        "worker_id": overrides.get("worker_id", "worker-1"),
        "worker_name": overrides.get("worker_name", "test-worker"),
        "platform": overrides.get("platform", "linux"),
        "suite_name": overrides.get("suite_name", "default"),
        "first_join": overrides.get("first_join", False),
        "results": overrides.get("results", [
            {
                "task_id": "task-1",
                "capability": "chat",
                "model": "llama3",
                "metric": "tokens_per_sec",
                "value": 42.0,
                "unit": "tok/s",
                "status": "ok",
                "elapsed_seconds": 5.0,
                "error": None,
                "measured_at": 1700000000.0,
                "details": {},
            }
        ]),
    }


@pytest.mark.asyncio
async def test_post_results_happy_path(client, app):
    store = _make_store()
    app.state.benchmark_store = store

    body = _report_payload(worker_id="w1", first_join=True)
    r = await client.post("/api/workers/w1/benchmark/results", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["worker_id"] == "w1"
    assert data["recorded"] == 1
    assert data["first_join"] is True


@pytest.mark.asyncio
async def test_post_results_store_not_initialised(client, app):
    app.state.benchmark_store = None

    body = _report_payload()
    r = await client.post("/api/workers/w1/benchmark/results", json=body)
    assert r.status_code == 503
    assert "error" in r.json()


@pytest.mark.asyncio
async def test_post_results_first_join_coerced_when_already_run(client, app):
    store = _make_store(has_first_join_run=True)
    app.state.benchmark_store = store

    body = _report_payload(worker_id="w1", first_join=True)
    r = await client.post("/api/workers/w1/benchmark/results", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["first_join"] is False


@pytest.mark.asyncio
async def test_post_results_empty_results(client, app):
    store = _make_store()
    app.state.benchmark_store = store

    body = _report_payload(results=[])
    r = await client.post("/api/workers/w1/benchmark/results", json=body)
    assert r.status_code == 200
    assert r.json()["recorded"] == 0


@pytest.mark.asyncio
async def test_post_results_multiple_results(client, app):
    store = _make_store(record_return=1)
    app.state.benchmark_store = store

    results = [
        {
            "task_id": f"task-{i}",
            "capability": "chat",
            "model": "llama3",
            "metric": "tokens_per_sec",
            "value": float(i * 10),
            "unit": "tok/s",
            "status": "ok",
            "elapsed_seconds": 5.0,
            "error": None,
            "measured_at": 1700000000.0 + i,
            "details": {},
        }
        for i in range(3)
    ]
    body = _report_payload(worker_id="w1", results=results)
    r = await client.post("/api/workers/w1/benchmark/results", json=body)
    assert r.status_code == 200
    assert r.json()["recorded"] == 3


@pytest.mark.asyncio
async def test_post_results_validation_error(client, app):
    store = _make_store()
    app.state.benchmark_store = store

    # Missing required fields (worker_id in body, results)
    body = {"first_join": False}
    r = await client.post("/api/workers/w1/benchmark/results", json=body)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_worker_benchmarks_happy_path(client, app):
    store = _make_store(
        latest_by_worker=[
            {
                "id": 1,
                "worker_id": "w1",
                "capability": "chat",
                "model": "llama3",
                "metric": "tokens_per_sec",
                "value": 42.0,
                "status": "ok",
                "first_join": True,
            }
        ],
        history_by_worker=[
            {
                "id": 1,
                "worker_id": "w1",
                "capability": "chat",
                "model": "llama3",
                "metric": "tokens_per_sec",
                "value": 42.0,
                "status": "ok",
                "first_join": True,
            }
        ],
    )
    app.state.benchmark_store = store

    r = await client.get("/api/workers/w1/benchmark")
    assert r.status_code == 200
    data = r.json()
    assert data["worker_id"] == "w1"
    assert "latest" in data
    assert "history" in data
    assert len(data["latest"]) == 1
    assert len(data["history"]) == 1


@pytest.mark.asyncio
async def test_get_worker_benchmarks_store_not_initialised(client, app):
    app.state.benchmark_store = None

    r = await client.get("/api/workers/w1/benchmark")
    assert r.status_code == 503
    assert "error" in r.json()


@pytest.mark.asyncio
async def test_get_worker_benchmarks_empty_history(client, app):
    store = _make_store(latest_by_worker=[], history_by_worker=[])
    app.state.benchmark_store = store

    r = await client.get("/api/workers/w1/benchmark")
    assert r.status_code == 200
    data = r.json()
    assert data["latest"] == []
    assert data["history"] == []


@pytest.mark.asyncio
async def test_get_worker_benchmarks_limit_param(client, app):
    store = _make_store(latest_by_worker=[], history_by_worker=[])
    app.state.benchmark_store = store

    r = await client.get("/api/workers/w1/benchmark?limit=5")
    assert r.status_code == 200
    store.history_by_worker.assert_awaited_with("w1", limit=5)


@pytest.mark.asyncio
async def test_get_capability_leaderboard_happy_path(client, app):
    store = _make_store(
        leaderboard=[
            {
                "id": 1,
                "worker_id": "w1",
                "capability": "chat",
                "metric": "tokens_per_sec",
                "value": 100.0,
                "status": "ok",
            },
            {
                "id": 2,
                "worker_id": "w2",
                "capability": "chat",
                "metric": "tokens_per_sec",
                "value": 80.0,
                "status": "ok",
            },
        ]
    )
    app.state.benchmark_store = store

    r = await client.get("/api/benchmarks/capability/chat")
    assert r.status_code == 200
    data = r.json()
    assert data["capability"] == "chat"
    assert "entries" in data
    assert len(data["entries"]) == 2


@pytest.mark.asyncio
async def test_get_capability_leaderboard_store_not_initialised(client, app):
    app.state.benchmark_store = None

    r = await client.get("/api/benchmarks/capability/chat")
    assert r.status_code == 503
    assert "error" in r.json()


@pytest.mark.asyncio
async def test_get_capability_leaderboard_with_metric(client, app):
    store = _make_store(leaderboard=[])
    app.state.benchmark_store = store

    r = await client.get(
        "/api/benchmarks/capability/chat?metric=tokens_per_sec"
    )
    assert r.status_code == 200
    store.leaderboard.assert_awaited_with(
        capability="chat", metric="tokens_per_sec"
    )


@pytest.mark.asyncio
async def test_get_capability_leaderboard_empty(client, app):
    store = _make_store(leaderboard=[])
    app.state.benchmark_store = store

    r = await client.get("/api/benchmarks/capability/chat")
    assert r.status_code == 200
    data = r.json()
    assert data["entries"] == []
