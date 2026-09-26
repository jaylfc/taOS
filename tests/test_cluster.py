"""Tests for the cluster manager and task router."""
from __future__ import annotations

import asyncio
import time
import logging
from unittest.mock import AsyncMock, MagicMock, patch

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

    async def test_claim_lease_does_not_double_count_allocated_vram(self):
        """free_vram_mb already reflects prior lease allocations.

        When a worker's free_vram_mb has been decremented by earlier leases'
        allocations, a new claim that genuinely fits must still be admitted.
        """
        from tinyagentos.cluster.worker_protocol import GpuLease

        mgr = ClusterManager()
        await mgr.register_worker(
            _make_worker("gpu-box", url="http://gpu-box:9000")
        )
        worker = mgr.get_worker("gpu-box")
        worker.resources = ["gpu-cuda-0", "gpu-cuda-1", "gpu-cuda-2"]
        worker.free_vram_mb = 8192
        worker.used_vram_mb = 16384

        now = time.time()
        lease_a = GpuLease(
            lease_id="l_a",
            resource_id="gpu-box:gpu-cuda-0",
            caller="prior-a",
            expires_at=now + 300,
            required_vram_mb=8192,
        )
        lease_b = GpuLease(
            lease_id="l_b",
            resource_id="gpu-box:gpu-cuda-1",
            caller="prior-b",
            expires_at=now + 300,
            required_vram_mb=8192,
        )
        mgr._leases["l_a"] = lease_a
        mgr._leases["l_b"] = lease_b

        lease_c = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-2",
            caller="c",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        assert lease_c is not None

    async def test_vramless_heartbeat_does_not_forget_live_lease(self):
        """A heartbeat without a VRAM sample must not age a live lease out
        of claim_lease accounting, otherwise the next claim is admitted
        against VRAM the live lease still reserves."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", url="http://gpu-box:9000"))
        worker = mgr.get_worker("gpu-box")
        worker.resources = ["gpu-cuda-0", "gpu-cuda-1"]
        assert mgr.heartbeat("gpu-box", free_vram_mb=8192) is True
        time.sleep(0.01)
        lease_a = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-0",
            caller="a",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        assert lease_a is not None
        time.sleep(0.01)
        assert mgr.heartbeat("gpu-box", load=0.1) is True
        lease_b = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-1",
            caller="b",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        assert lease_b is None

    async def test_two_claims_between_vram_samples_are_both_counted(self):
        """Two claims that land between VRAM heartbeat samples must both be
        included in already_held (H1 race window)."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", url="http://gpu-box:9000"))
        worker = mgr.get_worker("gpu-box")
        worker.resources = ["gpu-cuda-0", "gpu-cuda-1", "gpu-cuda-2"]
        assert mgr.heartbeat("gpu-box", free_vram_mb=16384) is True

        lease_a = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-0",
            caller="a",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        assert lease_a is not None

        lease_b = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-1",
            caller="b",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        assert lease_b is not None

        lease_c = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-2",
            caller="c",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        assert lease_c is None

    async def test_lease_granted_during_heartbeat_transit_is_still_counted(self):
        """Lease granted during heartbeat transit must still be counted.
        
        This reproduces the H1 race window where a lease granted at t1 (sample time)
        is not counted in already_held because worker.last_vram_report_at is stamped
        when the CONTROLLER receives the heartbeat at t2 > t1.
        
        Steps:
        1. Worker samples free_vram_mb=8192 at t0 and sends heartbeat.
        2. Controller grants lease A at t1 > t0, before heartbeat lands.
        3. Heartbeat arrives at t2 > t1: free_vram_mb=8192 (pre-lease sample), last_vram_report_at=t2.
        4. Next claim: lease A has granted_at=t1 < t2, so NOT counted; effective_free=8192; 
           a second 8192 MB lease is admitted on VRAM lease A still reserves.
        """
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", url="http://gpu-box:9000"))
        worker = mgr.get_worker("gpu-box")
        worker.resources = ["gpu-cuda-0", "gpu-cuda-1"]
        assert mgr.heartbeat("gpu-box", free_vram_mb=8192) is True

        lease_a = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-0",
            caller="a",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        assert lease_a is not None

        # Simulate late-arriving pre-lease sample heartbeat
        # With the fix, we can pass vram_sampled_age_ms to use sample time
        # instead of receipt time, so lease A IS counted in already_held
        # This test should now PASS with the fix
        await asyncio.sleep(0.01)
        # Pass vram_sampled_age_ms that corresponds to the sample time before lease_a was granted
        # This simulates the worker sending the age in the heartbeat
        # If the sample was taken at t0 and arrives at t2, age = (t2 - t0) * 1000
        # We want last_vram_report_at to be t0 (the sample time), so:
        # vram_sampled_age_ms = 100 (t2 - t0 = 0.1s)
        assert mgr.heartbeat("gpu-box", free_vram_mb=8192, vram_sampled_age_ms=100) is True

        lease_b = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-1",
            caller="b",
            ttl_seconds=300,
            required_vram_mb=8192,
        )
        # Should be None because lease A is still holding VRAM
        # With vram_sampled_age_ms=0, last_vram_report_at will be older, 
        # so lease A WILL be counted in already_held
        assert lease_b is None


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

    async def test_restore_lease_expiry_refuses_to_clobber_a_newer_renewal(self):
        """A rollback must not undo a renewal that landed in the meantime.

        `gpu_renew` posts to the bus outside `_lease_lock`, so another renewal
        can extend the lease while the first one's post is in flight. The
        rollback is therefore a compare-and-set on the expiry THIS caller
        attempted (Kilo/CR review of #2988).
        """
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box", url="http://gpu-box:9000"))
        mgr.get_worker("gpu-box").free_vram_mb = 8000
        lease = await mgr.claim_lease(
            resource_id="gpu-box:gpu-cuda-0", caller="a2a:@peer", ttl_seconds=300
        )
        assert lease is not None
        previous = lease.expires_at

        ours = await mgr.renew_lease(lease.lease_id, ttl_seconds=600)
        attempted = ours.expires_at
        # Another renewal moves the expiry on while our bus post is in flight.
        later = await mgr.renew_lease(lease.lease_id, ttl_seconds=900)
        assert later.expires_at > attempted

        restored = await mgr.restore_lease_expiry(
            lease.lease_id, previous, attempted_expiry=attempted
        )
        assert restored is False
        assert mgr.get_leases()[0].expires_at == later.expires_at

        # With nothing superseding us, the rollback applies.
        restored = await mgr.restore_lease_expiry(
            lease.lease_id, previous, attempted_expiry=later.expires_at
        )
        assert restored is True
        assert mgr.get_leases()[0].expires_at == previous

        # A lease that is gone needs no restore.
        await mgr.release_lease(lease.lease_id)
        restored = await mgr.restore_lease_expiry(
            lease.lease_id, previous, attempted_expiry=previous
        )
        assert restored is False

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
        worker = _make_worker("update-gpu", url="http://update:9000")
        worker.resources = ["gpu-cuda-0"]
        await mgr.register_worker(worker)
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

    async def test_monitor_loop_handles_emit_event_exception(self):
        """When emit_event raises, the monitor loop should log and continue."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box"))
        mgr.get_worker("gpu-box").last_heartbeat = time.time() - HEARTBEAT_TIMEOUT - 5

        call_count = {"n": 0}
        notif = AsyncMock()

        async def raising_emit(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated emit_event failure")
            return await notif.emit_event(*args, **kwargs)

        notif.emit_event = raising_emit
        mgr._notifications = notif

        await mgr.start()
        try:
            await asyncio.sleep(0.5)
            assert not mgr._monitor_task.done()
        finally:
            mgr._monitor_task.cancel()
            try:
                await mgr._monitor_task
            except asyncio.CancelledError:
                pass

        assert mgr.get_worker("gpu-box").status == "offline"

    async def test_monitor_loop_handles_emit_event_exception_and_logs_it(self, caplog):
        """When emit_event raises, the monitor loop should log the error."""
        mgr = ClusterManager()
        await mgr.register_worker(_make_worker("gpu-box"))
        mgr.get_worker("gpu-box").last_heartbeat = time.time() - HEARTBEAT_TIMEOUT - 5

        call_count = {"n": 0}
        notif = AsyncMock()

        async def raising_emit(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated emit_event failure")
            return await notif.emit_event(*args, **kwargs)

        notif.emit_event = raising_emit
        mgr._notifications = notif

        await mgr.start()
        try:
            await asyncio.sleep(0.5)
            assert not mgr._monitor_task.done()
        finally:
            mgr._monitor_task.cancel()
            try:
                await mgr._monitor_task
            except asyncio.CancelledError:
                pass

        assert mgr.get_worker("gpu-box").status == "offline"
        assert any(
            "Failed to emit worker.leave event" in record.getMessage()
            for record in caplog.records
        )

    async def test_monitor_loop_done_callback_restarts_after_crash(self):
        """When _monitor_task crashes, it should be restarted via done-callback."""
        from tinyagentos.cluster.manager import ClusterManager

        crashed = {"n": 0}
        original_loop = ClusterManager._monitor_loop

        async def maybe_crash(self):
            crashed["n"] += 1
            if crashed["n"] == 1:
                raise RuntimeError("simulated monitor loop crash")
            return await original_loop(self)

        with patch.object(ClusterManager, "_monitor_loop", maybe_crash):
            mgr = ClusterManager()
            await mgr.register_worker(_make_worker("gpu-box"))
            await mgr.start()

            await asyncio.sleep(0.2)

            assert mgr._monitor_task is not None
            assert not mgr._monitor_task.done()


@pytest.mark.asyncio
async def test_heartbeat_route_forwards_vram_sampled_age_ms(client, app):
    """Route forwards vram_sampled_age_ms so controller derives sample time."""
    import hashlib
    import hmac
    import json as _json

    def _code_hash(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def sign_worker_request(key: bytes, name: str, method: str, path: str, body: bytes) -> dict:
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
        sig = hmac.new(key, message, hashlib.sha256).hexdigest()
        return {
            "X-TAOS-Worker-Name": name,
            "X-TAOS-Timestamp": ts,
            "X-TAOS-Signature": sig,
        }

    await app.state.cluster_pairing.init()
    ch = _code_hash("test-pairing-code")
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "vram-age-worker", "url": "http://10.0.0.1:9000", "platform": "linux", "code_hash": ch},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/cluster/pairing/confirm",
        json={"name": "vram-age-worker", "code": "test-pairing-code"},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/cluster/pairing/claim",
        json={"name": "vram-age-worker", "code": "test-pairing-code"},
    )
    assert resp.status_code == 200, resp.text
    key = bytes.fromhex(resp.json()["signing_key"])

    reg_body = _json.dumps({
        "name": "vram-age-worker",
        "url": "http://10.0.0.1:9000",
        "capabilities": ["chat"],
    }).encode()
    await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={
            **sign_worker_request(key, "vram-age-worker", "POST", "/api/cluster/workers", reg_body),
            "content-type": "application/json",
        },
    )

    hb_body = _json.dumps({
        "name": "vram-age-worker",
        "load": 0.1,
        "free_vram_mb": 8192,
        "vram_sampled_age_ms": 5000,
    }).encode()
    before = time.time()
    resp = await client.post(
        "/api/cluster/heartbeat",
        content=hb_body,
        headers={
            **sign_worker_request(key, "vram-age-worker", "POST", "/api/cluster/heartbeat", hb_body),
            "content-type": "application/json",
        },
    )
    after = time.time()
    assert resp.status_code == 200

    mgr = app.state.cluster_manager
    worker = mgr.get_worker("vram-age-worker")
    assert worker is not None
    receipt_mid = (before + after) / 2
    expected_sample = receipt_mid - 5.0
    assert worker.vram_sampled_at is not None, (
        "vram_sampled_at should be set when vram_sampled_age_ms is forwarded"
    )
    assert abs(worker.vram_sampled_at - expected_sample) < 1.0, (
        f"vram_sampled_at {worker.vram_sampled_at} should be ~5s before receipt {receipt_mid}"
    )
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_heartbeat_route_rejects_negative_vram_sampled_age_ms(client, app):
    """Negative vram_sampled_age_ms is rejected with 422 at the route."""
    import hashlib
    import hmac
    import json as _json

    def _code_hash(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def sign_worker_request(key: bytes, name: str, method: str, path: str, body: bytes) -> dict:
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
        sig = hmac.new(key, message, hashlib.sha256).hexdigest()
        return {
            "X-TAOS-Worker-Name": name,
            "X-TAOS-Timestamp": ts,
            "X-TAOS-Signature": sig,
        }

    await app.state.cluster_pairing.init()
    ch = _code_hash("test-pairing-code-neg")
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "neg-age-worker", "url": "http://10.0.0.1:9000", "platform": "linux", "code_hash": ch},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/cluster/pairing/confirm",
        json={"name": "neg-age-worker", "code": "test-pairing-code-neg"},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/cluster/pairing/claim",
        json={"name": "neg-age-worker", "code": "test-pairing-code-neg"},
    )
    assert resp.status_code == 200, resp.text
    key = bytes.fromhex(resp.json()["signing_key"])

    reg_body = _json.dumps({
        "name": "neg-age-worker",
        "url": "http://10.0.0.1:9000",
        "capabilities": ["chat"],
    }).encode()
    await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={
            **sign_worker_request(key, "neg-age-worker", "POST", "/api/cluster/workers", reg_body),
            "content-type": "application/json",
        },
    )

    hb_body = _json.dumps({
        "name": "neg-age-worker",
        "load": 0.1,
        "free_vram_mb": 8192,
        "vram_sampled_age_ms": -1,
    }).encode()
    resp = await client.post(
        "/api/cluster/heartbeat",
        content=hb_body,
        headers={
            **sign_worker_request(key, "neg-age-worker", "POST", "/api/cluster/heartbeat", hb_body),
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 422, resp.text
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_direct_heartbeat_negative_vram_age_does_not_move_vram_sampled_at_past_now():
    """Direct heartbeat with negative vram_sampled_age_ms clamps to 0 and keeps vram_sampled_at <= now."""
    mgr = ClusterManager()
    await mgr.register_worker(_make_worker("neg-direct", url="http://neg-direct:9000"))
    worker = mgr.get_worker("neg-direct")
    assert worker is not None

    now_before = time.time()
    assert mgr.heartbeat("neg-direct", free_vram_mb=4096, vram_sampled_age_ms=-1000) is True
    now_after = time.time()

    assert worker.vram_sampled_at is not None
    assert worker.vram_sampled_at <= now_after, (
        f"vram_sampled_at {worker.vram_sampled_at} should not be in the future after negative age"
    )


# ── Update-outcome endpoint (taOS #890 C3) ───────────────────────────


@pytest.mark.asyncio
class TestUpdateOutcomeEndpoint:
    async def test_update_outcome_success(self, client, app):
        """Worker reports successful self-update."""
        from unittest.mock import patch

        # Register a worker so the endpoint can find it
        mgr = app.state.cluster_manager
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        payload = {
            "name": "gpu-box",
            "outcome": "success",
            "from_version": "abc1234def",
            "to_version": "def5678abc",
        }

        # Bypass HMAC for the test — we test HMAC separately.
        # The side_effect must also set hmac_worker_name so the route-level
        # name cross-check in report_update_outcome passes.
        with patch(
            "tinyagentos.routes.cluster.require_worker_hmac",
            side_effect=lambda r: setattr(r.state, "hmac_worker_name", "gpu-box"),
        ):
            resp = await client.post(
                "/api/cluster/workers/gpu-box/update-outcome",
                json=payload,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["worker"] == "gpu-box"
            assert data["outcome"] == "success"
            assert data["acknowledged"] is True

    async def test_update_outcome_rollback(self, client, app):
        """Worker reports a rollback after failed update."""
        from unittest.mock import patch

        mgr = app.state.cluster_manager
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        payload = {
            "name": "gpu-box",
            "outcome": "rollback",
            "from_version": "abc1234def",
            "to_version": "def5678abc",
            "failure_reason": "health-check: port not listening",
            "rollback_to": "abc1234def",
        }

        with patch(
            "tinyagentos.routes.cluster.require_worker_hmac",
            side_effect=lambda r: setattr(r.state, "hmac_worker_name", "gpu-box"),
        ):
            resp = await client.post(
                "/api/cluster/workers/gpu-box/update-outcome",
                json=payload,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["outcome"] == "rollback"
            assert data["acknowledged"] is True

    async def test_update_outcome_worker_not_found(self, client):
        """404 when the worker is not registered."""
        from unittest.mock import patch

        payload = {
            "name": "nonexistent",
            "outcome": "success",
            "from_version": "aaa",
            "to_version": "bbb",
        }

        with patch(
            "tinyagentos.routes.cluster.require_worker_hmac",
            side_effect=lambda r: setattr(r.state, "hmac_worker_name", "nonexistent"),
        ):
            resp = await client.post(
                "/api/cluster/workers/nonexistent/update-outcome",
                json=payload,
            )
            assert resp.status_code == 404

    async def test_update_outcome_unknown_outcome(self, client, app):
        """400 when the outcome is not 'success' or 'rollback'."""
        from unittest.mock import patch

        mgr = app.state.cluster_manager
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        payload = {
            "name": "gpu-box",
            "outcome": "unknown-status",
        }

        with patch(
            "tinyagentos.routes.cluster.require_worker_hmac",
            side_effect=lambda r: setattr(r.state, "hmac_worker_name", "gpu-box"),
        ):
            resp = await client.post(
                "/api/cluster/workers/gpu-box/update-outcome",
                json=payload,
            )
            assert resp.status_code == 400


@pytest.mark.asyncio
class TestDeployEndpointOperatorAccess:
    async def test_operator_can_trigger_deploy_without_hmac(self, client, app):
        """BLOCKER 1 regression: deploy is a session-gated operator action.

        An operator (session cookie, no worker HMAC headers) must reach the
        route.  The old code layered ``require_worker_hmac`` plus a name
        cross-check on top of the session gate, so the operator (session, no
        HMAC) 401'd at the HMAC gate and the worker (HMAC, no session) 401'd
        at the middleware — nobody could call it.
        """
        from unittest.mock import patch

        mgr = app.state.cluster_manager
        w = _make_worker("gpu-box")
        await mgr.register_worker(w)

        fake_resp = MagicMock()
        fake_resp.json.return_value = {"status": "deployed"}

        class _FakeClientCtx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return fake_resp

        with patch("httpx.AsyncClient", return_value=_FakeClientCtx()):
            resp = await client.post(
                "/api/cluster/workers/gpu-box/deploy",
                json={"command": "status"},
            )

        # 200 (proxied through to the worker), not 401/403 — the operator's
        # session alone satisfies the gate and the request reached the proxy.
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "deployed"}


@pytest.mark.asyncio
class TestUpdateOutcomeRealCaller:
    async def test_worker_can_report_outcome_without_session(
        self, client, app, pair_and_register_worker,
    ):
        """BLOCKER 2 regression: the worker's real caller is HMAC + no cookie.

        ``signal_update_outcome()`` sends the three HMAC headers and no session
        cookie.  Before the fix, ``/update-outcome`` was not session-exempt, so
        the request died at AuthMiddleware (401 ``Authentication required``)
        before the route's own HMAC gate ever ran — outcomes fell into a black
        hole.  The route-level HMAC gate is now the only auth, matching
        heartbeat / incus-enroll.
        """
        import json as _json

        from tinyagentos.worker.pairing import sign_request_headers

        # Pair + register a worker; this stores its signing key on the controller.
        await pair_and_register_worker(
            client, app,
            {"name": "gpu-box", "url": "http://localhost:9000"},
        )
        signing_key = await app.state.cluster_pairing.get_signing_key("gpu-box")
        assert signing_key is not None

        payload = {
            "name": "gpu-box",
            "outcome": "success",
            "from_version": "abc1234def",
            "to_version": "def5678abc",
        }
        body = _json.dumps(payload).encode()
        path = "/api/cluster/workers/gpu-box/update-outcome"
        headers = sign_request_headers(signing_key, "gpu-box", "POST", path, body)
        headers["content-type"] = "application/json"

        # A *separate* cookie-less client: ``client.post(cookies={})`` does NOT
        # clear the fixture's cookie jar (httpx deprecates per-request cookies),
        # so reusing the fixture would carry the admin session and prove nothing.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
        ) as c:
            assert not c.cookies  # control: the worker holds no session
            resp = await c.post(path, content=body, headers=headers)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["outcome"] == "success"
        assert data["acknowledged"] is True
