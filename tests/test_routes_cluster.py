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

async def _signed_enroll(client, key, name, incus_url, token):
    """POST a HMAC-signed incus-enroll request the way the worker does."""
    import json as _json
    path = f"/api/cluster/workers/{name}/incus-enroll"
    body = _json.dumps({"incus_url": incus_url, "token": token}).encode()
    headers = {
        **sign_worker_request(key, name, "POST", path, body),
        "content-type": "application/json",
    }
    return await client.post(path, content=body, headers=headers)


@pytest.mark.asyncio
async def test_incus_enroll_unsigned_rejected(client):
    """No HMAC headers -> 401 before the worker is even looked up."""
    resp = await client.post(
        "/api/cluster/workers/ghost-worker/incus-enroll",
        json={"incus_url": "https://10.0.0.5:8443", "token": "abc123"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_incus_enroll_worker_not_registered(client, app):
    """404 when the worker is paired (so the request is signable) but never registered."""
    key = await pair_worker(client, app, "ghost-worker", "https://10.0.0.5:9000")
    resp = await _signed_enroll(client, key, "ghost-worker", "https://10.0.0.5:8443", "abc123")
    assert resp.status_code == 404
    assert "not registered" in resp.json()["error"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_incus_enroll_name_mismatch_rejected(client, app):
    """A worker signing for a different worker's name -> 403."""
    key = await pair_worker(client, app, "worker-a", "https://10.0.0.7:9000")
    # Sign for worker-a but POST to worker-b's enroll path.
    import json as _json
    path = "/api/cluster/workers/worker-b/incus-enroll"
    body = _json.dumps({"incus_url": "https://10.0.0.7:8443", "token": "t"}).encode()
    headers = {
        **sign_worker_request(key, "worker-a", "POST", path, body),
        "content-type": "application/json",
    }
    resp = await client.post(path, content=body, headers=headers)
    assert resp.status_code == 403
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_incus_enroll_success(client, app):
    """Happy path: paired + registered worker signs the enroll -> 200."""
    import json as _json
    key = await pair_worker(client, app, "pi-worker", "http://10.0.0.5:9000")
    reg_body = _json.dumps({"name": "pi-worker", "url": "http://10.0.0.5:9000"}).encode()
    await client.post(
        "/api/cluster/workers", content=reg_body,
        headers={**sign_worker_request(key, "pi-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )

    mock_remote_add = AsyncMock(return_value={"success": True, "output": ""})
    with patch("tinyagentos.containers.remote_add", mock_remote_add):
        resp = await _signed_enroll(client, key, "pi-worker", "https://10.0.0.5:8443", "tok-xyz")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_remote_add.assert_awaited_once_with(
        "pi-worker", "https://10.0.0.5:8443", token="tok-xyz"
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
        resp = await _signed_enroll(client, key, "flaky-worker", "https://10.0.0.6:8443", "bad-tok")

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


# ---------------------------------------------------------------------------
# Worker auto-update: deploy-connection classification (taOS #1690)
# ---------------------------------------------------------------------------

async def _register_online_worker(app, name="gpu-box", url="http://gpu-box:9000"):
    from tinyagentos.cluster.worker_protocol import WorkerInfo
    await app.state.cluster_manager.register_worker(
        WorkerInfo(name=name, url=url, capabilities=["chat"],
                   load=0.0, status="online", platform="linux")
    )


def _client_raising(exc):
    """An httpx.AsyncClient stand-in whose .post always raises *exc*."""
    class _Raising:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise exc

    return _Raising


@pytest.mark.asyncio
async def test_update_worker_restart_disconnect_reports_updating(client, app, monkeypatch):
    """The deploy request blocks until update-worker restarts the worker, which
    drops the connection (RemoteProtocolError). That is the happy path: the
    update was accepted, so the route must return 200 status='updating' and
    keep the worker draining, not cancel the drain and return 502."""
    import httpx as _httpx
    await _register_online_worker(app)
    monkeypatch.setattr(
        _httpx, "AsyncClient",
        _client_raising(_httpx.RemoteProtocolError("server disconnected during restart")),
    )

    resp = await client.post("/api/cluster/workers/gpu-box/update")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "updating"
    # Drain must remain active so the monitor loop completes it on re-register.
    assert app.state.cluster_manager.get_worker("gpu-box").status == "draining"


@pytest.mark.asyncio
async def test_update_worker_connect_error_cancels_drain(client, app, monkeypatch):
    """Connection refused means the worker is unreachable and the update was
    never delivered: the route must cancel the drain (worker keeps serving)
    and return 502, not silently leave it draining."""
    import httpx as _httpx
    await _register_online_worker(app)
    monkeypatch.setattr(
        _httpx, "AsyncClient",
        _client_raising(_httpx.ConnectError("connection refused")),
    )

    resp = await client.post("/api/cluster/workers/gpu-box/update")
    assert resp.status_code == 502, resp.text
    assert resp.json().get("drain_cancelled") is True
    # Drain was cancelled, so the worker is back online and still routable.
    assert app.state.cluster_manager.get_worker("gpu-box").status == "online"


# ---------------------------------------------------------------------------
# Rolling "update all workers" orchestration (taOS #1876)
# ---------------------------------------------------------------------------


async def _register_workers(app, *names):
    """Register multiple online workers for testing update-all.

    Adds workers directly to the cluster manager's internal dict to avoid
    spawning background model-promotion tasks that can accumulate across
    concurrent async tests and cause event-loop deadlocks.
    """
    from tinyagentos.cluster.worker_protocol import WorkerInfo
    cluster = app.state.cluster_manager
    for i, name in enumerate(names):
        cluster._workers[name] = WorkerInfo(  # noqa: SLF001
            name=name, url=f"http://{name}:9000",
            capabilities=["chat"],
            load=0.0, status="online", platform="linux",
        )


async def _fake_sleep(seconds):
    """No-op sleep for tests -- makes re-registration polling instant."""
    pass


@pytest.mark.asyncio
async def test_update_all_workers_happy_path(client, app, monkeypatch):
    """Two online workers: both succeed via restart-disconnect, re-registration wait passes."""
    import asyncio as _asyncio
    await _register_workers(app, "w1", "w2")

    from tinyagentos.routes.cluster import _do_single_worker_update as _original_do

    call_order: list[str] = []

    async def _mock_do(cluster, worker):
        call_order.append(worker.name)
        # Simulate the drain→deploy step: set worker to draining, then return success.
        # The re-registration loop will poll and break once status is "online".
        # In the real helper, drain_worker sets draining; we simulate that here.
        worker.status = "draining"
        return {
            "success": True,
            "worker": worker.name,
            "status": "updating",
            "previous_status": "online",
            "drain_cancelled": False,
        }

    # Simulate re-registration: after the first poll, the worker comes back online.
    original_get_worker = app.state.cluster_manager.get_worker

    def _get_worker(name):
        w = original_get_worker(name)
        if w is not None and w.status == "draining":
            # Simulate re-registration: worker comes back online
            w.status = "online"
        return w

    monkeypatch.setattr(app.state.cluster_manager, "get_worker", _get_worker)
    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "tinyagentos.routes.cluster._do_single_worker_update",
        _mock_do,
    )

    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert sorted(data["updated"]) == ["w1", "w2"]
    assert data["failed"] == []
    assert data["total_targets"] == 2
    # Verify sequential: w1 must be processed before w2
    assert call_order == ["w1", "w2"]


@pytest.mark.asyncio
async def test_update_all_workers_one_fails_others_continue(client, app, monkeypatch):
    """w1 fails with ConnectError, w2 succeeds -- roll continues."""
    import asyncio as _asyncio
    await _register_workers(app, "w1", "w2")

    call_order: list[str] = []

    async def _mock_do(cluster, worker):
        call_order.append(worker.name)
        if worker.name == "w1":
            return {
                "success": False,
                "worker": "w1",
                "error": "Worker unreachable for update: test",
                "drain_cancelled": False,
            }
        worker.status = "draining"
        return {
            "success": True,
            "worker": worker.name,
            "status": "updating",
            "previous_status": "online",
            "drain_cancelled": False,
        }

    # Simulate re-registration for w2
    original_get_worker = app.state.cluster_manager.get_worker

    def _get_worker(name):
        w = original_get_worker(name)
        if w is not None and w.status == "draining":
            w.status = "online"
        return w

    monkeypatch.setattr(app.state.cluster_manager, "get_worker", _get_worker)
    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "tinyagentos.routes.cluster._do_single_worker_update",
        _mock_do,
    )

    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["updated"] == ["w2"]
    assert len(data["failed"]) == 1
    assert data["failed"][0]["name"] == "w1"
    assert "unreachable" in data["failed"][0]["error"]
    assert data["total_targets"] == 2
    # w1 must be processed before w2
    assert call_order == ["w1", "w2"]


@pytest.mark.asyncio
async def test_update_all_workers_skips_local(client, app, monkeypatch):
    """The 'local' worker is never targeted for update."""
    import asyncio as _asyncio
    await _register_workers(app, "r1", "r2")
    from tinyagentos.cluster.worker_protocol import WorkerInfo
    app.state.cluster_manager._workers["local"] = WorkerInfo(  # noqa: SLF001
        name="local", url="http://localhost:9000",
        capabilities=["chat"], load=0.0, status="online", platform="linux",
    )

    async def _mock_do(cluster, worker):
        worker.status = "draining"
        return {"success": True, "worker": worker.name, "drain_cancelled": False}

    # Simulate re-registration
    original_get_worker = app.state.cluster_manager.get_worker

    def _get_worker(name):
        w = original_get_worker(name)
        if w is not None and w.status == "draining":
            w.status = "online"
        return w

    monkeypatch.setattr(app.state.cluster_manager, "get_worker", _get_worker)
    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "tinyagentos.routes.cluster._do_single_worker_update",
        _mock_do,
    )

    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert sorted(data["updated"]) == ["r1", "r2"]
    # local must appear in skipped, not updated
    assert "local" in data["skipped"]
    assert "local" not in data["updated"]


@pytest.mark.asyncio
async def test_update_all_workers_skips_offline(client, app, monkeypatch):
    """Offline workers are skipped, online workers are updated."""
    import asyncio as _asyncio
    await _register_workers(app, "online-1", "online-2")
    app.state.cluster_manager._workers["online-2"].status = "offline"  # noqa: SLF001

    async def _mock_do(cluster, worker):
        worker.status = "draining"
        return {"success": True, "worker": worker.name, "drain_cancelled": False}

    # Simulate re-registration
    original_get_worker = app.state.cluster_manager.get_worker

    def _get_worker(name):
        w = original_get_worker(name)
        if w is not None and w.status == "draining":
            w.status = "online"
        return w

    monkeypatch.setattr(app.state.cluster_manager, "get_worker", _get_worker)
    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "tinyagentos.routes.cluster._do_single_worker_update",
        _mock_do,
    )

    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["updated"] == ["online-1"]
    assert "online-2" in data["skipped"]


@pytest.mark.asyncio
async def test_update_all_workers_no_online_workers(client, app):
    """When no online remote workers exist, return empty with a message."""
    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["updated"] == []
    assert data["failed"] == []
    assert data["skipped"] == []
    assert data["total_targets"] == 0
    assert "no online" in data.get("message", "").lower()


@pytest.mark.asyncio
async def test_update_all_workers_draining_excluded_from_skipped(client, app, monkeypatch):
    """Draining workers are not counted as skipped -- they are mid-update."""
    import asyncio as _asyncio
    await _register_workers(app, "w1", "w2")
    # Set w2 to draining -- it should NOT appear in skipped
    app.state.cluster_manager._workers["w2"].status = "draining"  # noqa: SLF001

    async def _mock_do(cluster, worker):
        worker.status = "draining"
        return {"success": True, "worker": worker.name, "drain_cancelled": False}

    original_get_worker = app.state.cluster_manager.get_worker

    def _get_worker(name):
        w = original_get_worker(name)
        if w is not None and w.status == "draining" and name != "w2":
            w.status = "online"
        return w

    monkeypatch.setattr(app.state.cluster_manager, "get_worker", _get_worker)
    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "tinyagentos.routes.cluster._do_single_worker_update",
        _mock_do,
    )

    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["updated"] == ["w1"]
    # w2 is draining -- NOT skipped
    assert "w2" not in data["skipped"]
    assert data["total_targets"] == 1  # only w1 was online


@pytest.mark.asyncio
async def test_update_all_workers_helper_exception_isolated(client, app, monkeypatch):
    """If _do_single_worker_update raises unexpectedly, roll continues."""
    import asyncio as _asyncio
    await _register_workers(app, "w1", "w2")

    call_order: list[str] = []

    async def _mock_do(cluster, worker):
        call_order.append(worker.name)
        if worker.name == "w1":
            raise RuntimeError("drain_worker exploded!")
        worker.status = "draining"
        return {
            "success": True,
            "worker": worker.name,
            "status": "updating",
            "previous_status": "online",
            "drain_cancelled": False,
        }

    original_get_worker = app.state.cluster_manager.get_worker

    def _get_worker(name):
        w = original_get_worker(name)
        if w is not None and w.status == "draining":
            w.status = "online"
        return w

    monkeypatch.setattr(app.state.cluster_manager, "get_worker", _get_worker)
    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "tinyagentos.routes.cluster._do_single_worker_update",
        _mock_do,
    )

    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["updated"] == ["w2"]
    assert len(data["failed"]) == 1
    assert data["failed"][0]["name"] == "w1"
    assert "RuntimeError" in data["failed"][0]["error"]
    assert data["total_targets"] == 2
    assert call_order == ["w1", "w2"]


@pytest.mark.asyncio
async def test_update_all_workers_re_register_timeout(client, app, monkeypatch):
    """When a worker never comes back online, the roll continues after timeout."""
    import asyncio as _asyncio
    await _register_workers(app, "w1", "w2")

    call_order: list[str] = []

    async def _mock_do(cluster, worker):
        call_order.append(worker.name)
        worker.status = "draining"
        return {
            "success": True,
            "worker": worker.name,
            "status": "updating",
            "previous_status": "online",
            "drain_cancelled": False,
        }

    # get_worker always returns "draining" -- simulates worker never coming back
    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "tinyagentos.routes.cluster._do_single_worker_update",
        _mock_do,
    )

    resp = await client.post("/api/cluster/workers/update-all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Both workers should still be listed as updated -- the timeout just logs a warning
    assert sorted(data["updated"]) == ["w1", "w2"]
    assert data["failed"] == []
    assert data["total_targets"] == 2
    assert call_order == ["w1", "w2"]


@pytest.mark.asyncio
async def test_register_returns_409_when_controller_fenced(client, app):
    """A fenced controller must refuse registration with 409, not claim success."""
    import json as _json
    key = await pair_worker(client, app, "fenced-worker", "http://10.0.0.1:9000")
    app.state.cluster_manager._fenced = True
    reg_body = _json.dumps({
        "name": "fenced-worker",
        "url": "http://10.0.0.1:9000",
        "capabilities": ["chat"],
    }).encode()
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**sign_worker_request(key, "fenced-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )
    assert resp.status_code == 409, resp.text
    assert "fenced" in resp.json().get("error", "").lower()
    workers = (await client.get("/api/cluster/workers")).json()
    assert not any(w["name"] == "fenced-worker" for w in workers)
    app.state.cluster_manager._fenced = False
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_register_returns_409_when_generation_mismatch(client, app):
    """A worker echoing a stale generation must be refused with 409."""
    import json as _json
    key = await pair_worker(client, app, "stale-gen-worker", "http://10.0.0.2:9000")
    app.state.cluster_manager._generation = 999
    reg_body = _json.dumps({
        "name": "stale-gen-worker",
        "url": "http://10.0.0.2:9000",
        "capabilities": ["chat"],
        "generation": 1,
    }).encode()
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**sign_worker_request(key, "stale-gen-worker", "POST", "/api/cluster/workers", reg_body), "content-type": "application/json"},
    )
    assert resp.status_code == 409, resp.text
    assert "generation" in resp.json().get("error", "").lower()
    workers = (await client.get("/api/cluster/workers")).json()
    assert not any(w["name"] == "stale-gen-worker" for w in workers)
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_update_all_workers_admin_gate_rejected(app, tmp_data_dir):
    """Unauthenticated callers are rejected with 401/403."""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/cluster/workers/update-all",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code in (401, 403)
