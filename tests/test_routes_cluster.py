"""Tests for the cluster API routes."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_worker_registration_api(client):
    body = {
        "name": "test-worker",
        "url": "http://192.168.1.50:9000",
        "platform": "linux",
        "capabilities": ["chat", "embed"],
        "hardware": {"cpu": "Ryzen 9", "ram_gb": 64},
        "models": ["llama3"],
    }
    resp = await client.post("/api/cluster/workers", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert data["name"] == "test-worker"

    # Verify it shows up in the list
    resp = await client.get("/api/cluster/workers")
    assert resp.status_code == 200
    workers = resp.json()
    assert len(workers) == 1
    assert workers[0]["name"] == "test-worker"
    assert workers[0]["status"] == "online"


@pytest.mark.asyncio
async def test_heartbeat_api(client):
    # Register first
    await client.post("/api/cluster/workers", json={
        "name": "hb-worker", "url": "http://10.0.0.1:9000", "capabilities": ["chat"],
    })

    # Send heartbeat
    resp = await client.post("/api/cluster/heartbeat", json={
        "name": "hb-worker", "load": 0.42, "models": ["phi3"],
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Verify updated values
    resp = await client.get("/api/cluster/workers")
    w = resp.json()[0]
    assert w["load"] == 0.42
    assert w["models"] == ["phi3"]


@pytest.mark.asyncio
async def test_heartbeat_unknown_worker(client):
    resp = await client.post("/api/cluster/heartbeat", json={"name": "ghost"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unregister_worker(client):
    await client.post("/api/cluster/workers", json={
        "name": "temp-worker", "url": "http://10.0.0.2:9000",
    })
    resp = await client.delete("/api/cluster/workers/temp-worker")
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"

    # Verify gone
    resp = await client.get("/api/cluster/workers")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_unregister_unknown_worker(client):
    resp = await client.delete("/api/cluster/workers/ghost")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_capabilities_api(client):
    await client.post("/api/cluster/workers", json={
        "name": "w1", "url": "http://10.0.0.1:9000", "capabilities": ["chat", "embed"],
    })
    await client.post("/api/cluster/workers", json={
        "name": "w2", "url": "http://10.0.0.2:9000", "capabilities": ["chat", "tts"],
    })

    resp = await client.get("/api/cluster/capabilities")
    assert resp.status_code == 200
    caps = resp.json()
    assert "chat" in caps
    assert sorted(caps["chat"]) == ["w1", "w2"]
    assert caps["embed"] == ["w1"]
    assert caps["tts"] == ["w2"]


@pytest.mark.asyncio
async def test_worker_registration_includes_kv_quant(client):
    body = {
        "name": "quant-worker",
        "url": "http://10.0.0.9:9000",
        "kv_cache_quant_support": ["fp16", "turboquant-k3v2"],
    }
    resp = await client.post("/api/cluster/workers", json=body)
    assert resp.status_code == 200

    resp = await client.get("/api/cluster/workers")
    workers = resp.json()
    assert len(workers) == 1
    assert workers[0]["kv_cache_quant_support"] == ["fp16", "turboquant-k3v2"]


@pytest.mark.asyncio
async def test_worker_registration_kv_quant_defaults_fp16(client):
    """A worker that doesn't send kv_cache_quant_support gets ["fp16"] by default."""
    body = {
        "name": "legacy-worker",
        "url": "http://10.0.0.8:9000",
        # no kv_cache_quant_support field
    }
    resp = await client.post("/api/cluster/workers", json=body)
    assert resp.status_code == 200

    resp = await client.get("/api/cluster/workers")
    workers = resp.json()
    assert workers[0]["kv_cache_quant_support"] == ["fp16"]


@pytest.mark.asyncio
async def test_heartbeat_updates_kv_quant(client):
    await client.post("/api/cluster/workers", json={
        "name": "kv-worker",
        "url": "http://10.0.0.7:9000",
        "kv_cache_quant_support": ["fp16"],
    })

    resp = await client.post("/api/cluster/heartbeat", json={
        "name": "kv-worker",
        "load": 0.1,
        "kv_cache_quant_support": ["fp16", "turboquant-k3v2"],
    })
    assert resp.status_code == 200

    resp = await client.get("/api/cluster/workers")
    w = resp.json()[0]
    assert "turboquant-k3v2" in w["kv_cache_quant_support"]


@pytest.mark.asyncio
async def test_kv_quant_options_empty_cluster(client):
    resp = await client.get("/api/cluster/kv-quant-options")
    assert resp.status_code == 200
    data = resp.json()
    assert "options" in data
    assert data["options"] == ["fp16"]


@pytest.mark.asyncio
async def test_kv_quant_options_all_fp16(client):
    for i in range(2):
        await client.post("/api/cluster/workers", json={
            "name": f"w{i}",
            "url": f"http://10.0.1.{i}:9000",
            "kv_cache_quant_support": ["fp16"],
        })
    resp = await client.get("/api/cluster/kv-quant-options")
    data = resp.json()
    assert data["options"] == ["fp16"]


@pytest.mark.asyncio
async def test_kv_quant_options_mixed_cluster(client):
    await client.post("/api/cluster/workers", json={
        "name": "plain",
        "url": "http://10.0.2.1:9000",
        "kv_cache_quant_support": ["fp16"],
    })
    await client.post("/api/cluster/workers", json={
        "name": "turboquant",
        "url": "http://10.0.2.2:9000",
        "kv_cache_quant_support": ["fp16", "turboquant-k3v2"],
    })
    resp = await client.get("/api/cluster/kv-quant-options")
    data = resp.json()
    assert "fp16" in data["options"]
    assert "turboquant-k3v2" in data["options"]


# ---------------------------------------------------------------------------
# incus-enroll endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_incus_enroll_worker_not_registered(client):
    """404 when the worker has never registered."""
    resp = await client.post(
        "/api/cluster/workers/ghost-worker/incus-enroll",
        json={"incus_url": "https://10.0.0.5:8443", "token": "abc123"},
    )
    assert resp.status_code == 404
    assert "not registered" in resp.json()["error"]


@pytest.mark.asyncio
async def test_incus_enroll_success(client):
    """Happy path: worker registered → remote_add called with right args → 200."""
    await client.post("/api/cluster/workers", json={
        "name": "pi-worker",
        "url": "http://10.0.0.5:9000",
    })

    mock_remote_add = AsyncMock(return_value={"success": True, "output": ""})
    with patch("tinyagentos.containers.remote_add", mock_remote_add):
        resp = await client.post(
            "/api/cluster/workers/pi-worker/incus-enroll",
            json={"incus_url": "https://10.0.0.5:8443", "token": "tok-xyz"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_remote_add.assert_awaited_once_with(
        "pi-worker", "https://10.0.0.5:8443", "tok-xyz"
    )


@pytest.mark.asyncio
async def test_incus_enroll_remote_add_failure(client):
    """remote_add returns failure → endpoint returns 500 with error text."""
    await client.post("/api/cluster/workers", json={
        "name": "flaky-worker",
        "url": "http://10.0.0.6:9000",
    })

    mock_remote_add = AsyncMock(return_value={
        "success": False,
        "output": "certificate rejected",
    })
    with patch("tinyagentos.containers.remote_add", mock_remote_add):
        resp = await client.post(
            "/api/cluster/workers/flaky-worker/incus-enroll",
            json={"incus_url": "https://10.0.0.6:8443", "token": "bad-tok"},
        )

    assert resp.status_code == 500
    data = resp.json()
    assert data["ok"] is False
    assert "certificate rejected" in data["error"]


# ---------------------------------------------------------------------------
# install-targets endpoint — tier_id and friendly_name (Task 11)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_install_targets_includes_controller_with_tier_id(client):
    resp = await client.get("/api/cluster/install-targets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    local = next(t for t in data if t["name"] == "local")
    assert local["type"] == "local"
    assert local["label"] == "This controller"
    assert "tier_id" in local
    # Controller's tier comes from app.state.hardware_profile — accept any
    # non-empty string; specific value depends on the host running tests.
    assert isinstance(local["tier_id"], str) and local["tier_id"]
    assert "friendly_name" in local
    assert local["friendly_name"] == "Controller"


@pytest.mark.asyncio
async def test_install_targets_remote_includes_tier_id(app, client, monkeypatch):
    # Register a fake worker so /api/cluster/workers has something with a
    # tier_id we control.
    # WorkerInfo.hardware is a plain dict (worker agent sends raw hardware data).
    # Use ram_mb + a npu string so worker_tier_id() produces a non-empty arm-npu-*gb id.
    from tinyagentos.cluster.worker_protocol import WorkerInfo
    cluster = app.state.cluster_manager
    fake_worker = WorkerInfo(
        name="orange-pi",
        url="https://192.168.1.10:8443",
        hardware={
            "ram_mb": 16384,
            "npu": {"type": "rk3588"},
            "cpu": {"arch": "aarch64"},
            "gpu": {},
        },
        status="online",
    )
    cluster._workers["orange-pi"] = fake_worker  # noqa: SLF001

    # Pretend an incus remote with the same name is registered.
    async def fake_remote_list():
        return [{"name": "orange-pi", "addr": "https://192.168.1.10:8443",
                 "protocol": "incus"}]
    monkeypatch.setattr(
        "tinyagentos.containers.remote_list", fake_remote_list
    )

    resp = await client.get("/api/cluster/install-targets")
    assert resp.status_code == 200
    data = resp.json()
    pi = next((t for t in data if t["name"] == "orange-pi"), None)
    assert pi is not None
    assert pi["type"] == "remote"
    assert pi["addr"] == "https://192.168.1.10:8443"
    # tier_id should be derived from the worker's hardware via
    # _potential_capabilities — exact value depends on registry, but
    # the key must be present and non-empty.
    assert "tier_id" in pi
    assert isinstance(pi["tier_id"], str) and pi["tier_id"]
    assert pi["friendly_name"] == "orange-pi"


@pytest.mark.asyncio
async def test_install_targets_matches_remote_to_worker_by_url_host(app, client, monkeypatch):
    """When the incus remote name (e.g. 'fedora-worker') doesn't equal the
    cluster worker name (e.g. 'fedora-host'), the install-target lookup
    must still link them via URL hostname so the box doesn't show as
    'unknown hardware'."""
    from tinyagentos.cluster.worker_protocol import WorkerInfo
    cluster = app.state.cluster_manager
    cluster._workers["fedora-host"] = WorkerInfo(  # noqa: SLF001
        name="fedora-host",
        url="https://192.168.6.108:8443",
        hardware={
            "ram_mb": 65536,
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "vram_mb": 16384, "cuda": True},
        },
        status="online",
    )

    async def fake_remote_list():
        return [{"name": "fedora-worker", "addr": "https://192.168.6.108:8443",
                 "protocol": "incus"}]
    monkeypatch.setattr(
        "tinyagentos.containers.remote_list", fake_remote_list
    )

    resp = await client.get("/api/cluster/install-targets")
    assert resp.status_code == 200
    data = resp.json()
    fedora = next((t for t in data if t["name"] == "fedora-worker"), None)
    assert fedora is not None
    assert fedora["hardware_known"] is True, fedora
    assert fedora["tier_id"] not in ("", "unknown"), fedora
