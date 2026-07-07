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
        assert await mgr.unregister_worker("gpu-box") is True
        assert mgr.get_workers() == []
        assert await mgr.unregister_worker("gpu-box") is False

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
