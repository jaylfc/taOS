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

    async def test_heartbeat_does_not_reonline_draining_worker(self):
        """A worker mid graceful-drain keeps heartbeating; heartbeat must NOT
        flip it back to online or it re-enters routing before leases finish
        (taOS #890)."""
        mgr = ClusterManager()
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)
        w.status = "draining"

        ok = mgr.heartbeat("gpu-box", load=0.1)
        assert ok is True
        # Load/last_heartbeat still update, but status stays draining.
        updated = mgr.get_worker("gpu-box")
        assert updated.status == "draining"
        assert updated.load == 0.1

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

    async def test_monitor_loop_never_offlines_local_worker(self):
        """The controller's own 'local' worker is kept alive by
        local_heartbeat_loop, not remote heartbeats. The monitor loop must
        skip it so a heartbeat gap never marks the controller offline (which
        would drop its own leases and remove local backends from routing).
        A regular stale worker in the same pass is still marked offline
        (taOS #1690)."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("local"))
        await mgr.register_worker(_make_worker("gpu-box"))
        stale = time.time() - HEARTBEAT_TIMEOUT - 5
        mgr.get_worker("local").last_heartbeat = stale
        mgr.get_worker("gpu-box").last_heartbeat = stale

        task = asyncio.create_task(mgr._monitor_loop())
        try:
            await asyncio.sleep(0.05)  # let one iteration run
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert mgr.get_worker("local").status == "online"    # guard held
        assert mgr.get_worker("gpu-box").status == "offline"  # normal path

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


@pytest.mark.asyncio
class TestWorkerDrain:
    """Tests for taOS #890 — worker auto-update with graceful drain."""

    async def test_drain_worker_graceful_sets_draining_status(self):
        """Graceful drain sets status to 'draining', leaves leases intact."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        result = await mgr.drain_worker("gpu-box", graceful=True)
        assert result["worker"] == "gpu-box"
        assert result["previous_status"] == "online"
        assert result["status"] == "draining"
        assert result["released_leases"] == 0
        assert mgr.get_worker("gpu-box").status == "draining"

    async def test_drain_worker_force_releases_leases_and_marks_offline(self):
        """Force drain releases all leases and marks worker offline."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        # Add a lease for this worker
        from tinyagentos.cluster.worker_protocol import GpuLease
        import time
        lease = GpuLease(
            lease_id="l_test1",
            resource_id="gpu-box:gpu-cuda-0",
            caller="test",
            expires_at=time.time() + 60,
            required_vram_mb=0,
        )
        mgr._leases["l_test1"] = lease

        result = await mgr.drain_worker("gpu-box", graceful=False)
        assert result["released_leases"] == 1
        assert result["status"] == "offline"
        assert mgr.get_worker("gpu-box").status == "offline"
        assert "l_test1" not in mgr._leases

    async def test_drain_worker_unknown_returns_error(self):
        """Draining a non-existent worker returns error dict."""
        mgr = ClusterManager()
        result = await mgr.drain_worker("nonexistent")
        assert "error" in result
        assert result["worker"] == "nonexistent"

    async def test_cancel_drain_returns_worker_to_online(self):
        """Cancel drain returns a draining worker back to online."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))
        await mgr.drain_worker("gpu-box", graceful=True)
        assert mgr.get_worker("gpu-box").status == "draining"

        result = await mgr.cancel_drain("gpu-box")
        assert result["worker"] == "gpu-box"
        assert result["status"] == "online"
        assert mgr.get_worker("gpu-box").status == "online"

    async def test_cancel_drain_only_works_on_draining(self):
        """Cancel drain returns error for non-draining workers."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box"))

        result = await mgr.cancel_drain("gpu-box")
        assert "error" in result
        assert "not draining" in result["error"]

    async def test_cancel_drain_unknown_returns_error(self):
        """Cancel drain on unknown worker returns error."""
        mgr = ClusterManager()
        result = await mgr.cancel_drain("nonexistent")
        assert "error" in result

    async def test_draining_workers_excluded_from_routing(self):
        """Draining workers should not receive new tasks."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("online-gpu", capabilities=["chat"], load=0.1))
        await mgr.register_worker(_make_worker("draining-gpu", capabilities=["chat"], load=0.0))
        await mgr.drain_worker("draining-gpu", graceful=True)

        result = mgr.get_workers_for_capability("chat")
        assert len(result) == 1
        assert result[0].name == "online-gpu"

    async def test_draining_workers_excluded_from_catalog(self):
        """Draining workers are excluded from the aggregate catalog."""
        mgr = ClusterManager()
        online = _make_worker("online", capabilities=["chat"])
        online.backends = [{"name": "b1", "type": "ollama", "url": "u", "capabilities": ["chat"], "models": [{"name": "m1"}]}]
        draining = _make_worker("draining", capabilities=["chat"])
        draining.backends = [{"name": "b2", "type": "ollama", "url": "u", "capabilities": ["chat"], "models": [{"name": "m2"}]}]
        await mgr.register_worker(online)
        await mgr.register_worker(draining)
        await mgr.drain_worker("draining", graceful=True)

        out = mgr.aggregate_catalog()
        assert [w["name"] for w in out["workers"]] == ["online"]
        assert [m["name"] for m in out["models"]] == ["m1"]

    async def test_draining_workers_excluded_from_lease_claim(self):
        """Lease claims should fail for draining workers."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("draining-gpu", url="http://draining:9000"))

        # Set up worker with VRAM info
        worker = mgr.get_worker("draining-gpu")
        worker.free_vram_mb = 8000

        await mgr.drain_worker("draining-gpu", graceful=True)

        lease = await mgr.claim_lease(
            resource_id="draining-gpu:gpu-cuda-0",
            caller="test",
            ttl_seconds=30,
        )
        assert lease is None

    # ── Worker-initiated drain (taOS #890 C2) ──────────────────────────

    async def test_heartbeat_status_draining_triggers_worker_self_drain(self):
        """When a worker heartbeats with status='draining', it enters
        draining state — identical to a controller-initiated drain."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        ok = mgr.heartbeat("gpu-box", load=0.1, status="draining", drain_reason="update")
        assert ok is True
        worker = mgr.get_worker("gpu-box")
        assert worker.status == "draining"

    async def test_heartbeat_status_update_available_sets_status(self):
        """Worker reports an update is available but stays routable."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        ok = mgr.heartbeat("gpu-box", load=0.1, status="update-available", drain_reason="update")
        assert ok is True
        worker = mgr.get_worker("gpu-box")
        assert worker.status == "update-available"

    async def test_update_available_workers_still_routable(self):
        """Workers in update-available status should still receive tasks."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("update-gpu", capabilities=["chat"], load=0.1))
        await mgr.register_worker(_make_worker("draining-gpu", capabilities=["chat"], load=0.0))

        # Transition via heartbeat
        mgr.heartbeat("update-gpu", status="update-available")
        await mgr.drain_worker("draining-gpu", graceful=True)

        result = mgr.get_workers_for_capability("chat")
        assert len(result) == 1
        assert result[0].name == "update-gpu"

    async def test_update_available_workers_in_catalog(self):
        """Update-available workers are included in the aggregate catalog."""
        mgr = ClusterManager()
        online = _make_worker("online", capabilities=["chat"])
        online.backends = [{"name": "b1", "type": "ollama", "url": "u", "capabilities": ["chat"], "models": [{"name": "m1"}]}]
        updating = _make_worker("updating", capabilities=["chat"])
        updating.backends = [{"name": "b2", "type": "ollama", "url": "u", "capabilities": ["chat"], "models": [{"name": "m2"}]}]
        await mgr.register_worker(online)
        await mgr.register_worker(updating)
        mgr.heartbeat("updating", status="update-available")

        out = mgr.aggregate_catalog()
        worker_names = [w["name"] for w in out["workers"]]
        assert "online" in worker_names
        assert "updating" in worker_names
        model_names = [m["name"] for m in out["models"]]
        assert "m1" in model_names
        assert "m2" in model_names

    async def test_update_available_can_claim_leases(self):
        """Update-available workers still allow lease claims."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("update-gpu", url="http://update:9000"))
        worker = mgr.get_worker("update-gpu")
        worker.free_vram_mb = 8000
        mgr.heartbeat("update-gpu", status="update-available")

        lease = await mgr.claim_lease(
            resource_id="update-gpu:gpu-cuda-0",
            caller="test",
            ttl_seconds=30,
        )
        assert lease is not None
        assert lease.resource_id == "update-gpu:gpu-cuda-0"

    async def test_state_transition_online_to_update_available_to_draining(self):
        """Full state transition: online → update-available → draining."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        # online → update-available
        mgr.heartbeat("gpu-box", status="update-available", drain_reason="new version v2")
        assert mgr.get_worker("gpu-box").status == "update-available"

        # update-available → draining (worker initiates drain)
        mgr.heartbeat("gpu-box", status="draining", drain_reason="new version v2")
        assert mgr.get_worker("gpu-box").status == "draining"

    async def test_drain_preserved_on_subsequent_heartbeats(self):
        """Once draining, subsequent heartbeats without status preserve drain."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        # Initiate self-drain
        mgr.heartbeat("gpu-box", status="draining", drain_reason="update")
        assert mgr.get_worker("gpu-box").status == "draining"

        # Normal heartbeat (no status field) — should stay draining
        mgr.heartbeat("gpu-box", load=0.5)
        assert mgr.get_worker("gpu-box").status == "draining"
        assert mgr.get_worker("gpu-box").load == 0.5

    async def test_heartbeat_without_status_preserves_update_available(self):
        """A status-less heartbeat preserves update-available (taOS #890 C2).

        The periodic ~15s heartbeat carries no status; when combined with the
        old manager.py:226-231 logic that flipped any non-draining worker back
        to "online", this silently cancelled the update signal before the
        worker could initiate its drain.  Now "update-available" is protected
        in the same way "draining" is: only an explicit status transition
        (e.g. "draining" or "updating") changes it."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        mgr.heartbeat("gpu-box", status="update-available")
        assert mgr.get_worker("gpu-box").status == "update-available"

        # Normal heartbeat without explicit status — should stay update-available
        mgr.heartbeat("gpu-box", load=0.2)
        assert mgr.get_worker("gpu-box").status == "update-available"
        assert mgr.get_worker("gpu-box").load == 0.2

        # Explicit transition to draining still works
        mgr.heartbeat("gpu-box", status="draining", drain_reason="update")
        assert mgr.get_worker("gpu-box").status == "draining"

    async def test_monitor_loop_offlines_stale_update_available_worker(self):
        """An update-available worker that stops heartbeating is marked offline."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box"))
        mgr.heartbeat("gpu-box", status="update-available")
        # Simulate stale heartbeat
        mgr.get_worker("gpu-box").last_heartbeat = time.time() - HEARTBEAT_TIMEOUT - 5

        now = time.time()
        for worker in mgr._workers.values():
            if worker.status in ("online", "update-available") and (now - worker.last_heartbeat) > HEARTBEAT_TIMEOUT:
                worker.status = "offline"

        assert mgr.get_worker("gpu-box").status == "offline"

    async def test_heartbeat_status_updating_sets_status(self):
        """Worker reports it is actively applying an update."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))

        ok = mgr.heartbeat("gpu-box", status="updating", drain_reason="update")
        assert ok is True
        assert mgr.get_worker("gpu-box").status == "updating"

    async def test_updating_workers_excluded_from_routing(self):
        """Updating workers should not receive new tasks."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("online-gpu", capabilities=["chat"], load=0.1))
        await mgr.register_worker(_make_worker("updating-gpu", capabilities=["chat"], load=0.0))
        mgr.heartbeat("updating-gpu", status="updating")

        result = mgr.get_workers_for_capability("chat")
        assert len(result) == 1
        assert result[0].name == "online-gpu"
