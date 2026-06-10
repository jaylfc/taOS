"""Tests for the cluster API routes."""
from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Pairing helpers (shared with test_routes_cluster_pairing.py)
# ---------------------------------------------------------------------------

def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def sign_worker_request(
    key: bytes,
    name: str,
    method: str,
    path: str,
    body: bytes,
) -> dict:
    """Return the three HMAC auth headers for a worker request."""
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
    sig = hmac.new(key, message, hashlib.sha256).hexdigest()
    return {
        "X-TAOS-Worker-Name": name,
        "X-TAOS-Timestamp": ts,
        "X-TAOS-Signature": sig,
    }


async def pair_worker(
    client: AsyncClient,
    app,
    name: str,
    url: str,
    platform: str = "linux",
    code: str = "test-pairing-code",
) -> bytes:
    """Drive announce -> confirm -> claim and return the signing key."""
    await app.state.cluster_pairing.init()
    ch = _code_hash(code)
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": name, "url": url, "platform": platform, "code_hash": ch},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/cluster/pairing/confirm",
        json={"name": name, "code": code},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/cluster/pairing/claim",
        json={"name": name, "code": code},
    )
    assert resp.status_code == 200, resp.text
    return bytes.fromhex(resp.json()["signing_key"])


@pytest.mark.asyncio
async def test_worker_registration_api(client, app):
    key = await pair_worker(client, app, "test-worker", "http://192.168.1.50:9000")
    import json as _json
    reg_body = _json.dumps({
        "name": "test-worker",
        "url": "http://192.168.1.50:9000",
        "platform": "linux",
        "capabilities": ["chat", "embed"],
        "hardware": {"cpu": "Ryzen 9", "ram_gb": 64},
        "models": ["llama3"],
    }).encode()
    headers = sign_worker_request(key, "test-worker", "POST", "/api/cluster/workers", reg_body)
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**headers, "content-type": "application/json"},
    )
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
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_heartbeat_api(client, app):
    import json as _json
    key = await pair_worker(client, app, "hb-worker", "http://10.0.0.1:9000")
    # Register first
    reg_body = _json.dumps({"name": "hb-worker", "url": "http://10.0.0.1:9000", "capabilities": ["chat"]}).encode()
    await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**sign_worker_request(key, "hb-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )

    # Send heartbeat
    hb_body = _json.dumps({"name": "hb-worker", "load": 0.42, "models": ["phi3"]}).encode()
    resp = await client.post(
        "/api/cluster/heartbeat",
        content=hb_body,
        headers={**sign_worker_request(key, "hb-worker", "POST", "/api/cluster/heartbeat", hb_body), "content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Verify updated values
    resp = await client.get("/api/cluster/workers")
    w = resp.json()[0]
    assert w["load"] == 0.42
    assert w["models"] == ["phi3"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_heartbeat_unknown_worker(client, app):
    import json as _json
    # Pair so the HMAC gate passes, but never register so the heartbeat 404s
    key = await pair_worker(client, app, "ghost", "http://10.0.0.99:9000")
    hb_body = _json.dumps({"name": "ghost"}).encode()
    resp = await client.post(
        "/api/cluster/heartbeat",
        content=hb_body,
        headers={**sign_worker_request(key, "ghost", "POST", "/api/cluster/heartbeat", hb_body), "content-type": "application/json"},
    )
    assert resp.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_unregister_worker(client, app):
    import json as _json
    key = await pair_worker(client, app, "temp-worker", "http://10.0.0.2:9000")
    reg_body = _json.dumps({"name": "temp-worker", "url": "http://10.0.0.2:9000"}).encode()
    await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**sign_worker_request(key, "temp-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )
    resp = await client.delete("/api/cluster/workers/temp-worker")
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"

    # Verify gone
    resp = await client.get("/api/cluster/workers")
    assert len(resp.json()) == 0
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_unregister_unknown_worker(client):
    resp = await client.delete("/api/cluster/workers/ghost")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_capabilities_api(client, app):
    import json as _json
    key1 = await pair_worker(client, app, "w1", "http://10.0.0.1:9000")
    reg1 = _json.dumps({"name": "w1", "url": "http://10.0.0.1:9000", "capabilities": ["chat", "embed"]}).encode()
    await client.post(
        "/api/cluster/workers", content=reg1,
        headers={**sign_worker_request(key1, "w1", "POST", "/api/cluster/workers", reg1), "content-type": "application/json"},
    )
    key2 = await pair_worker(client, app, "w2", "http://10.0.0.2:9000", code="other-code")
    reg2 = _json.dumps({"name": "w2", "url": "http://10.0.0.2:9000", "capabilities": ["chat", "tts"]}).encode()
    await client.post(
        "/api/cluster/workers", content=reg2,
        headers={**sign_worker_request(key2, "w2", "POST", "/api/cluster/workers", reg2), "content-type": "application/json"},
    )

    resp = await client.get("/api/cluster/capabilities")
    assert resp.status_code == 200
    caps = resp.json()
    assert "chat" in caps
    assert sorted(caps["chat"]) == ["w1", "w2"]
    assert caps["embed"] == ["w1"]
    assert caps["tts"] == ["w2"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_worker_registration_includes_kv_quant(client, app):
    import json as _json
    key = await pair_worker(client, app, "quant-worker", "http://10.0.0.9:9000")
    reg_body = _json.dumps({
        "name": "quant-worker",
        "url": "http://10.0.0.9:9000",
        "kv_cache_quant_support": ["fp16", "turboquant-k3v2"],
    }).encode()
    resp = await client.post(
        "/api/cluster/workers", content=reg_body,
        headers={**sign_worker_request(key, "quant-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )
    assert resp.status_code == 200

    resp = await client.get("/api/cluster/workers")
    workers = resp.json()
    assert len(workers) == 1
    assert workers[0]["kv_cache_quant_support"] == ["fp16", "turboquant-k3v2"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_worker_registration_kv_quant_defaults_fp16(client, app):
    """A worker that doesn't send kv_cache_quant_support gets ["fp16"] by default."""
    import json as _json
    key = await pair_worker(client, app, "legacy-worker", "http://10.0.0.8:9000")
    reg_body = _json.dumps({
        "name": "legacy-worker",
        "url": "http://10.0.0.8:9000",
        # no kv_cache_quant_support field
    }).encode()
    resp = await client.post(
        "/api/cluster/workers", content=reg_body,
        headers={**sign_worker_request(key, "legacy-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )
    assert resp.status_code == 200

    resp = await client.get("/api/cluster/workers")
    workers = resp.json()
    assert workers[0]["kv_cache_quant_support"] == ["fp16"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_heartbeat_updates_kv_quant(client, app):
    import json as _json
    key = await pair_worker(client, app, "kv-worker", "http://10.0.0.7:9000")
    reg_body = _json.dumps({
        "name": "kv-worker",
        "url": "http://10.0.0.7:9000",
        "kv_cache_quant_support": ["fp16"],
    }).encode()
    await client.post(
        "/api/cluster/workers", content=reg_body,
        headers={**sign_worker_request(key, "kv-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )

    hb_body = _json.dumps({
        "name": "kv-worker",
        "load": 0.1,
        "kv_cache_quant_support": ["fp16", "turboquant-k3v2"],
    }).encode()
    resp = await client.post(
        "/api/cluster/heartbeat", content=hb_body,
        headers={**sign_worker_request(key, "kv-worker", "POST", "/api/cluster/heartbeat", hb_body), "content-type": "application/json"},
    )
    assert resp.status_code == 200

    resp = await client.get("/api/cluster/workers")
    w = resp.json()[0]
    assert "turboquant-k3v2" in w["kv_cache_quant_support"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_kv_quant_options_empty_cluster(client):
    resp = await client.get("/api/cluster/kv-quant-options")
    assert resp.status_code == 200
    data = resp.json()
    assert "options" in data
    assert data["options"] == ["fp16"]


@pytest.mark.asyncio
async def test_kv_quant_options_all_fp16(client, app):
    import json as _json
    for i in range(2):
        name = f"w{i}"
        url = f"http://10.0.1.{i}:9000"
        code = f"code-w{i}"
        key = await pair_worker(client, app, name, url, code=code)
        reg_body = _json.dumps({"name": name, "url": url, "kv_cache_quant_support": ["fp16"]}).encode()
        await client.post(
            "/api/cluster/workers", content=reg_body,
            headers={**sign_worker_request(key, name, "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
        )
    resp = await client.get("/api/cluster/kv-quant-options")
    data = resp.json()
    assert data["options"] == ["fp16"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_kv_quant_options_mixed_cluster(client, app):
    import json as _json
    key_plain = await pair_worker(client, app, "plain", "http://10.0.2.1:9000")
    reg_plain = _json.dumps({"name": "plain", "url": "http://10.0.2.1:9000", "kv_cache_quant_support": ["fp16"]}).encode()
    await client.post(
        "/api/cluster/workers", content=reg_plain,
        headers={**sign_worker_request(key_plain, "plain", "POST", "/api/cluster/workers", reg_plain), "content-type": "application/json"},
    )
    key_tq = await pair_worker(client, app, "turboquant", "http://10.0.2.2:9000", code="tq-code")
    reg_tq = _json.dumps({"name": "turboquant", "url": "http://10.0.2.2:9000", "kv_cache_quant_support": ["fp16", "turboquant-k3v2"]}).encode()
    await client.post(
        "/api/cluster/workers", content=reg_tq,
        headers={**sign_worker_request(key_tq, "turboquant", "POST", "/api/cluster/workers", reg_tq), "content-type": "application/json"},
    )
    resp = await client.get("/api/cluster/kv-quant-options")
    data = resp.json()
    assert "fp16" in data["options"]
    assert "turboquant-k3v2" in data["options"]
    await app.state.cluster_pairing.close()


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
async def test_incus_enroll_success(client, app):
    """Happy path: worker registered then incus-enroll called with right args -> 200."""
    import json as _json
    key = await pair_worker(client, app, "pi-worker", "http://10.0.0.5:9000")
    reg_body = _json.dumps({"name": "pi-worker", "url": "http://10.0.0.5:9000"}).encode()
    await client.post(
        "/api/cluster/workers", content=reg_body,
        headers={**sign_worker_request(key, "pi-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )

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
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_incus_enroll_remote_add_failure(client, app):
    """remote_add returns failure -> endpoint returns 500 with error text."""
    import json as _json
    key = await pair_worker(client, app, "flaky-worker", "http://10.0.0.6:9000")
    reg_body = _json.dumps({"name": "flaky-worker", "url": "http://10.0.0.6:9000"}).encode()
    await client.post(
        "/api/cluster/workers", content=reg_body,
        headers={**sign_worker_request(key, "flaky-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )

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
    await app.state.cluster_pairing.close()


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
