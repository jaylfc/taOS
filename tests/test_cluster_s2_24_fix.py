"""Tests for S2-24 worker resource inventory and lease cap."""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo


def _make_worker(name: str, resources: list[str] | None = None,
                 capabilities: list[str] | None = None,
                 load: float = 0.0, status: str = "online",
                 url: str = "http://localhost:9000") -> WorkerInfo:
    return WorkerInfo(
        name=name,
        url=url,
        capabilities=capabilities or ["chat", "embed"],
        resources=resources or [],
        load=load,
        status=status,
        platform="linux",
    )


@pytest.mark.asyncio
class TestS224_ResourceInventory:
    async def test_worker_cannot_claim_fabricated_resources(self):
        """Register a worker with real resources, fabricated claims are rejected."""
        mgr = ClusterManager()
        worker = _make_worker("test-worker", resources=["gpu-cuda-0", "cpu-inference"])
        await mgr.register_worker(worker)

        for i in range(10):
            lease = await mgr.claim_lease(f"test-worker:fabricated-{i}", caller="test")
            assert lease is None, f"Fabricated resource fabricated-{i} should be rejected"

        valid_leases = 0
        for rid in ["test-worker:gpu-cuda-0", "test-worker:cpu-inference"]:
            lease = await mgr.claim_lease(rid, caller="test")
            assert lease is not None
            valid_leases += 1
        assert valid_leases == 2

    async def test_worker_lease_cap_counts_active_only(self):
        """Lease cap counts only active (non-expired) leases."""
        mgr = ClusterManager()
        worker = _make_worker("cap-worker", resources=[f"gpu-cuda-{i}" for i in range(12)])
        await mgr.register_worker(worker)

        for i in range(10):
            lease = await mgr.claim_lease(f"cap-worker:gpu-cuda-{i}", caller=f"caller-{i}", ttl_seconds=0.001)
            assert lease is not None

        time.sleep(0.1)
        active = [l for l in mgr._leases.values() if l.expires_at > time.time()]
        assert len(active) == 0

        lease11 = await mgr.claim_lease("cap-worker:gpu-cuda-10", caller="caller-11")
        assert lease11 is not None

    async def test_resource_validation_against_inventory(self):
        """_worker_for_resource validates against resources inventory."""
        mgr = ClusterManager()
        worker = _make_worker("gpu-worker", resources=["gpu-cuda-0", "cpu-inference"])
        await mgr.register_worker(worker)

        assert mgr._worker_for_resource("gpu-worker:gpu-cuda-0") is not None
        assert mgr._worker_for_resource("gpu-worker:cpu-inference") is not None
        assert mgr._worker_for_resource("gpu-worker:fake-resource") is None
        assert mgr._worker_for_resource("gpu-worker:gpu-cuda-99") is None


@pytest.mark.asyncio
class TestS224_Routes:
    async def test_register_route_rejects_an_unknown_resource_class(self, client, app):
        """The controller must not store an operator-supplied junk class.

        install-worker.sh exports TAOS_WORKER_RESOURCES, so the inventory is an
        operator knob; a typo there used to be stored verbatim and rendered in
        the cluster UI (and consulted on every lease claim).
        """
        from test_routes_cluster_pairing import pair_worker, sign_worker_request
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "bad-res", "http://bad-res:9000")
        body = {
            "name": "bad-res",
            "url": "http://bad-res:9000",
            "resources": ["cpu-inference", "gpu-cuda-99; rm -rf /"],
        }
        body_bytes = json.dumps(body).encode()
        path = "/api/cluster/workers"
        headers = sign_worker_request(key, "bad-res", "POST", path, body_bytes)
        headers["content-type"] = "application/json"
        resp = await client.post(path, content=body_bytes, headers=headers)
        assert resp.status_code == 400, resp.text
        assert "resource" in resp.json()["error"], resp.text
        assert app.state.cluster_manager.get_worker("bad-res") is None

    async def test_register_route_accepts_gpu_metal(self, client, app):
        """gpu-metal is the class #37 registers on Apple Silicon."""
        from test_routes_cluster_pairing import pair_worker, sign_worker_request
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "mac-res", "http://mac-res:9000")
        body = {
            "name": "mac-res",
            "url": "http://mac-res:9000",
            "resources": ["gpu-metal", "cpu-inference"],
        }
        body_bytes = json.dumps(body).encode()
        path = "/api/cluster/workers"
        headers = sign_worker_request(key, "mac-res", "POST", path, body_bytes)
        headers["content-type"] = "application/json"
        resp = await client.post(path, content=body_bytes, headers=headers)
        assert resp.status_code == 200, resp.text
        assert app.state.cluster_manager.get_worker("mac-res").resources == [
            "gpu-metal",
            "cpu-inference",
        ]

    async def test_heartbeat_route_rejects_an_unknown_resource_class(self, client, app):
        """A bad heartbeat class is refused and leaves the stored list intact."""
        from test_routes_cluster_pairing import pair_worker, sign_worker_request
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "hb-bad", "http://hb-bad:9000")
        reg = {
            "name": "hb-bad",
            "url": "http://hb-bad:9000",
            "resources": ["cpu-inference"],
        }
        reg_bytes = json.dumps(reg).encode()
        reg_path = "/api/cluster/workers"
        reg_headers = sign_worker_request(key, "hb-bad", "POST", reg_path, reg_bytes)
        reg_headers["content-type"] = "application/json"
        resp = await client.post(reg_path, content=reg_bytes, headers=reg_headers)
        assert resp.status_code == 200, resp.text

        hb = {"name": "hb-bad", "resources": ["cpu-inference", "not-a-class"]}
        hb_bytes = json.dumps(hb).encode()
        hb_path = "/api/cluster/heartbeat"
        hb_headers = sign_worker_request(key, "hb-bad", "POST", hb_path, hb_bytes)
        hb_headers["content-type"] = "application/json"
        resp = await client.post(hb_path, content=hb_bytes, headers=hb_headers)
        assert resp.status_code == 400, resp.text
        assert app.state.cluster_manager.get_worker("hb-bad").resources == [
            "cpu-inference"
        ]

    async def test_register_route_populates_resources(self, client, app):
        """POST /api/cluster/workers with resources stores them on WorkerInfo."""
        from test_routes_cluster_pairing import pair_worker, sign_worker_request
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "res-worker", "http://res-worker:9000")
        body = {
            "name": "res-worker",
            "url": "http://res-worker:9000",
            "resources": ["gpu-cuda-0", "cpu-inference"],
        }
        body_bytes = json.dumps(body).encode()
        path = "/api/cluster/workers"
        headers = sign_worker_request(key, "res-worker", "POST", path, body_bytes)
        headers["content-type"] = "application/json"
        resp = await client.post(path, content=body_bytes, headers=headers)
        assert resp.status_code == 200
        mgr = app.state.cluster_manager
        w = mgr.get_worker("res-worker")
        assert w is not None
        assert w.resources == ["gpu-cuda-0", "cpu-inference"]

    async def test_heartbeat_route_populates_resources(self, client, app):
        """POST /api/cluster/heartbeat with resources replaces the worker's list."""
        from test_routes_cluster_pairing import pair_worker, sign_worker_request
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "hb-worker", "http://hb-worker:9000")
        body = {
            "name": "hb-worker",
            "url": "http://hb-worker:9000",
            "resources": ["gpu-cuda-0"],
        }
        body_bytes = json.dumps(body).encode()
        path = "/api/cluster/workers"
        headers = sign_worker_request(key, "hb-worker", "POST", path, body_bytes)
        headers["content-type"] = "application/json"
        resp = await client.post(path, content=body_bytes, headers=headers)
        assert resp.status_code == 200
        hb_body = {
            "name": "hb-worker",
            "resources": ["npu-rk3588", "cpu-inference"],
        }
        hb_bytes = json.dumps(hb_body).encode()
        hb_path = "/api/cluster/heartbeat"
        hb_headers = sign_worker_request(key, "hb-worker", "POST", hb_path, hb_bytes)
        hb_headers["content-type"] = "application/json"
        resp = await client.post(hb_path, content=hb_bytes, headers=hb_headers)
        assert resp.status_code == 200
        mgr = app.state.cluster_manager
        w = mgr.get_worker("hb-worker")
        assert w is not None
        assert w.resources == ["npu-rk3588", "cpu-inference"]


@pytest.mark.asyncio
class TestS224_WorkerAgent:
    async def test_worker_agent_register_sends_resources(self, monkeypatch):
        """WorkerAgent.register() payload includes discovered resources."""
        # install-worker.sh exports TAOS_WORKER_RESOURCES; the exact-list
        # assertion below only holds with no installer classes in the env.
        monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
        from tinyagentos.worker.agent import WorkerAgent
        captured = {}

        from dataclasses import dataclass

        @dataclass
        class FakeHW:
            ram_mb: int = 0
            cpu: dict = None
            gpu: dict = None
            npu: dict = None

        class MockClient:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, **kwargs):
                captured["url"] = url
                captured["body"] = json.loads(kwargs["content"])
                resp = AsyncMock()
                resp.status_code = 200
                resp.json.return_value = {"status": "registered", "generation": 1}
                resp.raise_for_status = AsyncMock()
                return resp

        agent = WorkerAgent(
            controller_url="http://controller:9000",
            name="test-agent",
            worker_port=9000,
        )
        with patch("tinyagentos.worker.pairing.load_signing_key", return_value=b"fake-key"):
            with patch("httpx.AsyncClient", MockClient):
                with patch("tinyagentos.hardware.detect_hardware", return_value=FakeHW()):
                    with patch.object(agent, "detect_backends", new_callable=AsyncMock, return_value=[]):
                        with patch.object(agent, "detect_capabilities", return_value=[]):
                            with patch.object(agent, "detect_kv_quant_support", return_value={"legacy": ["fp16"], "k": ["fp16"], "v": ["fp16"], "boundary": False}):
                                result = await agent.register()
        assert result is True
        assert "resources" in captured["body"]
        assert captured["body"]["resources"] == ["cpu-inference"]

    async def test_worker_agent_heartbeat_sends_resources(self, monkeypatch):
        """WorkerAgent.heartbeat() payload includes discovered resources."""
        # Same env-independence as the register case above.
        monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
        from tinyagentos.worker.agent import WorkerAgent
        captured = {}

        from dataclasses import dataclass

        @dataclass
        class FakeHW:
            ram_mb: int = 0
            cpu: dict = None
            gpu: dict = None
            npu: dict = None

        class MockClient:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, **kwargs):
                captured["url"] = url
                captured["body"] = json.loads(kwargs["content"])
                resp = AsyncMock()
                resp.status_code = 200
                resp.json.return_value = {"status": "ok", "generation": 1}
                resp.raise_for_status = AsyncMock()
                return resp

        agent = WorkerAgent(
            controller_url="http://controller:9000",
            name="test-agent",
            worker_port=9000,
        )
        agent._registered = True
        with patch("tinyagentos.worker.pairing.load_signing_key", return_value=b"fake-key"):
            with patch("httpx.AsyncClient", MockClient):
                with patch("tinyagentos.hardware.detect_hardware", return_value=FakeHW()):
                    with patch.object(agent, "detect_backends", new_callable=AsyncMock, return_value=[]):
                        with patch.object(agent, "detect_capabilities", return_value=[]):
                            with patch.object(agent, "detect_kv_quant_support", return_value={"legacy": ["fp16"], "k": ["fp16"], "v": ["fp16"], "boundary": False}):
                                with patch("tinyagentos.cluster.worker_capacity.capacity_snapshot", return_value={"storage_cap_bytes": 0, "storage_used_bytes": 0, "bytes_deduped_total": 0}):
                                    with patch("tinyagentos.cluster.worker_capacity.gpu_vram_snapshot", return_value=None):
                                        with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=0.0):
                                            status = await agent.heartbeat()
        assert status == 200
        assert "resources" in captured["body"]
        assert captured["body"]["resources"] == ["cpu-inference"]


# --- resource-class grammar (docs/design/resource-scheduler.md) -----------


def test_resource_class_grammar_matches_the_documented_classes():
    """One grammar for the register validation and the lease fallback."""
    from tinyagentos.cluster.worker_protocol import RESOURCE_CLASS_RE

    for valid in (
        "cpu-inference",
        "gpu-metal",
        "gpu-mali",
        "gpu-cuda-0",
        "gpu-cuda-11",
        "gpu-rocm-0",
        "gpu-vulkan-0",
        "gpu-sycl-0",
        "npu-rk3588",
        "accel-hailo",
    ):
        assert RESOURCE_CLASS_RE.match(valid), valid

    for invalid in (
        "",
        "cpu",
        "GPU-METAL",
        "gpu-cuda",
        "gpu-metal-1",
        "npu-",
        "accel-",
        "gpu-cuda-0 ",
        "gpu-cuda-0\n",
        "cpu-inference\n",
        "gpu-cuda-99; rm -rf /",
        "TAOS_WORKER_RESOURCES",
    ):
        assert not RESOURCE_CLASS_RE.match(invalid), invalid


def test_invalid_resource_classes_lists_only_the_offenders():
    from tinyagentos.cluster.worker_protocol import invalid_resource_classes

    assert invalid_resource_classes(None) == []
    assert invalid_resource_classes([]) == []
    assert invalid_resource_classes(["cpu-inference", "gpu-metal"]) == []
    assert invalid_resource_classes(["cpu-inference", "bogus"]) == ["bogus"]


@pytest.mark.asyncio
class TestS224_LegacyFallback:
    async def test_worker_without_an_inventory_can_claim_gpu_metal(self):
        """The lease-time fallback grammar must accept the Apple Silicon class.

        The local regex this replaced predated gpu-metal, so a Mac that
        registered without an inventory could not lease its own resource.
        """
        mgr = ClusterManager()
        worker = _make_worker("mac-worker")
        await mgr.register_worker(worker)
        assert worker.resources == []
        lease = await mgr.claim_lease("mac-worker:gpu-metal", caller="test")
        assert lease is not None, "the fallback grammar rejected gpu-metal"
        assert await mgr.claim_lease("mac-worker:nonsense", caller="test") is None
