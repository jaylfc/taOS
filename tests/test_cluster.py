"""Tests for the cluster manager and task router."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tinyagentos.cluster.manager import ClusterManager, HEARTBEAT_TIMEOUT
from tinyagentos.cluster.router import TaskRouter
from tinyagentos.cluster.worker_protocol import WorkerInfo


def _make_worker(name: str, capabilities: list[str] | None = None,
                 load: float = 0.0, status: str = "online",
                 url: str = "http://localhost:9000") -> WorkerInfo:
    return WorkerInfo(
        name=name,
        url=url,
        capabilities=capabilities or ["chat", "embed"],
        load=load,
        status=status,
        platform="linux",
    )


@pytest.mark.asyncio
class TestClusterManager:
    async def test_register_worker(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        assert len(mgr.get_workers()) == 1
        fetched = mgr.get_worker("gpu-box")
        assert fetched is not None
        assert fetched.status == "online"
        assert fetched.registered_at > 0
        assert fetched.last_heartbeat > 0

    async def test_unregister_worker(self):
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box"))
        assert mgr.unregister_worker("gpu-box") is True
        assert mgr.get_workers() == []
        assert mgr.unregister_worker("gpu-box") is False

    async def test_heartbeat_updates_load_and_status(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        ok = mgr.heartbeat("gpu-box", load=0.75, models=["llama3"])
        assert ok is True
        updated = mgr.get_worker("gpu-box")
        assert updated.load == 0.75
        assert updated.models == ["llama3"]
        assert updated.status == "online"

    async def test_heartbeat_unknown_worker_returns_false(self):
        mgr = ClusterManager()
        assert mgr.heartbeat("nonexistent") is False

    async def test_heartbeat_revives_offline_worker(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)
        w.status = "offline"

        mgr.heartbeat("gpu-box", load=0.1)
        assert mgr.get_worker("gpu-box").status == "online"

    async def test_heartbeat_timeout_marks_offline(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)
        # Simulate stale heartbeat
        w.last_heartbeat = time.time() - HEARTBEAT_TIMEOUT - 5

        # Run what the monitor loop would do
        now = time.time()
        for worker in mgr._workers.values():
            if worker.status == "online" and (now - worker.last_heartbeat) > HEARTBEAT_TIMEOUT:
                worker.status = "offline"

        assert mgr.get_worker("gpu-box").status == "offline"

    async def test_get_workers_for_capability_filters_and_sorts(self):
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("fast-gpu", capabilities=["chat", "embed"], load=0.2))
        await mgr.register_worker(_make_worker("slow-gpu", capabilities=["chat"], load=0.8))
        await mgr.register_worker(_make_worker("offline-gpu", capabilities=["chat"], load=0.0))
        # Mark one offline
        mgr.get_worker("offline-gpu").status = "offline"

        result = mgr.get_workers_for_capability("chat")
        assert len(result) == 2
        assert result[0].name == "fast-gpu"  # lowest load first
        assert result[1].name == "slow-gpu"

    async def test_get_workers_for_capability_embed(self):
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("fast-gpu", capabilities=["chat", "embed"], load=0.2))
        await mgr.register_worker(_make_worker("slow-gpu", capabilities=["chat"], load=0.1))

        result = mgr.get_workers_for_capability("embed")
        assert len(result) == 1
        assert result[0].name == "fast-gpu"

    async def test_get_best_worker_returns_lowest_load(self):
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("high-load", capabilities=["chat"], load=0.9))
        await mgr.register_worker(_make_worker("low-load", capabilities=["chat"], load=0.1))

        best = mgr.get_best_worker("chat")
        assert best is not None
        assert best.name == "low-load"

    async def test_get_best_worker_returns_none_for_missing_capability(self):
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu", capabilities=["chat"]))
        assert mgr.get_best_worker("tts") is None

    async def test_aggregate_catalog_unions_workers(self):
        """The cluster-wide aggregate view should union live backend
        catalogs across every online worker."""
        mgr = ClusterManager()

        pi = WorkerInfo(
            name="pi4",
            url="http://pi:6970",
            platform="linux-aarch64",
            capabilities=["embedding", "llm-chat"],
            backends=[
                {
                    "name": "llama-cpp@http://localhost:8000",
                    "type": "llama-cpp",
                    "url": "http://localhost:8000",
                    "capabilities": ["embedding", "llm-chat"],
                    "models": [{"name": "qwen2.5-1.5b", "size_mb": 1200}],
                    "status": "ok",
                },
            ],
        )
        fedora = WorkerInfo(
            name="fedora",
            url="http://fedora:6970",
            platform="linux-x86_64",
            capabilities=["llm-chat", "image-generation"],
            backends=[
                {
                    "name": "sd-cpp@http://localhost:7864",
                    "type": "sd-cpp",
                    "url": "http://localhost:7864",
                    "capabilities": ["image-generation"],
                    "models": [{"name": "sdxl-turbo", "size_mb": 6500}],
                    "status": "ok",
                },
                {
                    "name": "ollama@http://localhost:11434",
                    "type": "ollama",
                    "url": "http://localhost:11434",
                    "capabilities": ["llm-chat"],
                    "models": [{"name": "gemma-2-9b", "size_mb": 5500}],
                    "status": "ok",
                },
            ],
        )
        await mgr.register_worker(pi)
        await mgr.register_worker(fedora)

        out = mgr.aggregate_catalog()
        assert len(out["workers"]) == 2
        assert len(out["backends"]) == 3
        # Capabilities union
        assert set(out["capabilities"]) == {"embedding", "llm-chat", "image-generation"}
        # Every model is tagged with its owning worker
        models_by_worker = {}
        for m in out["models"]:
            models_by_worker.setdefault(m["worker"], []).append(m["name"])
        assert models_by_worker["pi4"] == ["qwen2.5-1.5b"]
        assert sorted(models_by_worker["fedora"]) == ["gemma-2-9b", "sdxl-turbo"]

    async def test_aggregate_catalog_skips_offline_workers(self):
        """Offline workers are excluded — their stale data would mislead routing."""
        mgr = ClusterManager()
        online = _make_worker("online", capabilities=["chat"])
        online.backends = [{"name": "b1", "type": "ollama", "url": "u", "capabilities": ["chat"], "models": [{"name": "m1"}]}]
        offline = _make_worker("offline", capabilities=["chat"])
        offline.backends = [{"name": "b2", "type": "ollama", "url": "u", "capabilities": ["chat"], "models": [{"name": "m2"}]}]
        await mgr.register_worker(online)
        await mgr.register_worker(offline)
        offline.status = "offline"

        out = mgr.aggregate_catalog()
        assert [w["name"] for w in out["workers"]] == ["online"]
        assert [m["name"] for m in out["models"]] == ["m1"]

    async def test_aggregate_catalog_empty_cluster(self):
        """An empty or fully-offline cluster returns empty lists, never errors."""
        mgr = ClusterManager()
        out = mgr.aggregate_catalog()
        assert out == {
            "workers": [],
            "backends": [],
            "capabilities": [],
            "models": [],
        }


@pytest.mark.asyncio
class TestTaskRouter:
    @pytest.mark.asyncio
    async def test_router_tries_workers_in_order(self):
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("w1", capabilities=["chat"], load=0.1, url="http://w1:8000"))
        await mgr.register_worker(_make_worker("w2", capabilities=["chat"], load=0.5, url="http://w2:8000"))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        # First worker fails, second succeeds
        fail_resp = MagicMock()
        fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        ok_resp.json.return_value = {"result": "ok"}
        mock_client.post.side_effect = [fail_resp, ok_resp]

        router = TaskRouter(mgr, mock_client)
        data, worker_name = await router.route_request("chat", "POST", "/v1/chat/completions", {"messages": []})

        assert data == {"result": "ok"}
        assert worker_name == "w2"
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_router_returns_none_when_all_fail(self):
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("w1", capabilities=["chat"], load=0.1, url="http://w1:8000"))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = Exception("connection refused")

        router = TaskRouter(mgr, mock_client)
        data, worker_name = await router.route_request("chat", "POST", "/v1/chat/completions", {})

        assert data is None
        assert worker_name is None

    @pytest.mark.asyncio
    async def test_router_returns_none_for_no_workers(self):
        mgr = ClusterManager()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        router = TaskRouter(mgr, mock_client)

        data, worker_name = await router.route_request("chat", "POST", "/v1/chat/completions", {})
        assert data is None
        assert worker_name is None


# ---------------------------------------------------------------------------
# ClusterManager — drain_worker / cancel_drain (taOS #1707)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestClusterManagerDrain:
    async def test_drain_worker_graceful_marks_draining(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        result = await mgr.drain_worker("gpu-box", graceful=True)
        assert result["worker"] == "gpu-box"
        assert result["previous_status"] == "online"
        assert result["status"] == "draining"
        assert result["released_leases"] == 0

        worker = mgr.get_worker("gpu-box")
        assert worker.status == "draining"

    async def test_drain_worker_not_found(self):
        mgr = ClusterManager()
        result = await mgr.drain_worker("nonexistent")
        assert result["error"] == "worker not found"

    async def test_drain_worker_force_releases_leases(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        # Create a lease for this worker
        lease = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-0",
            caller="test",
            ttl_seconds=300,
            required_vram_mb=1024,
        )
        assert lease is not None

        result = await mgr.drain_worker("gpu-box", graceful=False)
        assert result["worker"] == "gpu-box"
        assert result["status"] == "offline"
        assert result["released_leases"] == 1

        worker = mgr.get_worker("gpu-box")
        assert worker.status == "offline"

        # Lease should be gone
        leases = mgr.get_leases()
        assert len(leases) == 0

    async def test_cancel_drain_puts_worker_back_online(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        await mgr.drain_worker("gpu-box", graceful=True)
        assert mgr.get_worker("gpu-box").status == "draining"

        result = await mgr.cancel_drain("gpu-box")
        assert result["status"] == "online"
        assert mgr.get_worker("gpu-box").status == "online"

    async def test_cancel_drain_not_draining(self):
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        result = await mgr.cancel_drain("gpu-box")
        assert "error" in result
        assert "not draining" in result["error"]

    async def test_cancel_drain_not_found(self):
        mgr = ClusterManager()
        result = await mgr.cancel_drain("nonexistent")
        assert result["error"] == "worker not found"

    async def test_draining_workers_excluded_from_routing(self):
        mgr = ClusterManager()
        await mgr.register_worker(
            _make_worker("online-w", capabilities=["chat"], load=0.1)
        )
        await mgr.register_worker(
            _make_worker("draining-w", capabilities=["chat"], load=0.1)
        )
        await mgr.drain_worker("draining-w", graceful=True)

        workers = mgr.get_workers_for_capability("chat")
        worker_names = [w.name for w in workers]
        assert "online-w" in worker_names
        assert "draining-w" not in worker_names

    async def test_set_gpu_arbiter_cross_wiring(self):
        mgr = ClusterManager()
        # set_gpu_arbiter is called by application wiring code;
        # verify it doesn't crash and stores the reference.
        mock_arbiter = MagicMock()
        mgr.set_gpu_arbiter(mock_arbiter)
        assert mgr._gpu_arbiter is mock_arbiter

    async def test_drain_force_calls_arbiter_cancel(self):
        from unittest.mock import AsyncMock

        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        mock_arbiter = MagicMock()
        mock_arbiter.cancel_running_for_leases = AsyncMock(
            return_value=(2, 0)
        )
        mgr.set_gpu_arbiter(mock_arbiter)

        # Create leases
        await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-0", caller="test",
            ttl_seconds=300, required_vram_mb=1024,
        )
        await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-1", caller="test",
            ttl_seconds=300, required_vram_mb=2048,
        )

        result = await mgr.drain_worker("gpu-box", graceful=False)
        assert result["released_leases"] == 2
        mock_arbiter.cancel_running_for_leases.assert_called_once()
        call_args = mock_arbiter.cancel_running_for_leases.call_args[0][0]
        assert len(call_args) == 2  # two lease IDs
