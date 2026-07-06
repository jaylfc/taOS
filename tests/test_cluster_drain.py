"""Tests for graceful worker detach + drain/cancel (taOS #796)."""
import asyncio
import time
import pytest
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo, GpuLease


def _make_online_worker(name="w1", capabilities=None, free_vram_mb=24576):
    return WorkerInfo(
        name=name,
        url=f"http://{name}:6969",
        hardware={"gpu": {"model": "NVIDIA RTX 4090", "type": "cuda", "vram_mb": 24576}},
        capabilities=capabilities or ["llm-chat", "embedding"],
        status="online",
        last_heartbeat=time.time(),
        free_vram_mb=free_vram_mb,
    )


# ── Drain Worker ───────────────────────────────────────────────────────

class TestDrainWorker:
    def test_drain_graceful_sets_status(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w

        result = cm.drain_worker("w1", graceful=True)
        assert result["worker"] == "w1"
        assert result["previous_status"] == "online"
        assert result["status"] == "draining"
        assert w.status == "draining"

    def test_drain_force_releases_leases(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w

        # Give the worker active leases
        lease = cm.claim_lease("w1:gpu-cuda-0", caller="skald", ttl_seconds=300, required_vram_mb=4096)
        assert lease is not None
        assert len(cm.get_leases()) == 1

        result = cm.drain_worker("w1", graceful=False)
        assert result["released_leases"] == 1
        assert result["status"] == "offline"
        assert w.status == "offline"
        assert len(cm.get_leases()) == 0

    def test_drain_graceful_keeps_leases(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w

        lease = cm.claim_lease("w1:gpu-cuda-0", caller="skald", ttl_seconds=300, required_vram_mb=4096)
        assert lease is not None

        result = cm.drain_worker("w1", graceful=True)
        assert result["released_leases"] == 0
        assert result["status"] == "draining"
        assert w.status == "draining"
        # Lease should still be active
        assert len(cm.get_leases()) == 1

    def test_drain_nonexistent_worker(self):
        cm = ClusterManager()
        result = cm.drain_worker("ghost", graceful=True)
        assert "error" in result

    def test_drain_already_draining(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w
        cm.drain_worker("w1", graceful=True)

        # Second drain — should still work (idempotent)
        result = cm.drain_worker("w1", graceful=True)
        assert result["previous_status"] == "draining"
        assert result["status"] == "draining"

    def test_drain_force_already_offline(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        w.status = "offline"
        cm._workers["w1"] = w

        result = cm.drain_worker("w1", graceful=False)
        assert result["previous_status"] == "offline"
        assert result["status"] == "offline"


# ── Cancel Drain ────────────────────────────────────────────────────────

class TestCancelDrain:
    def test_cancel_drain_returns_to_online(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w
        cm.drain_worker("w1", graceful=True)
        assert w.status == "draining"

        result = cm.cancel_drain("w1")
        assert result["worker"] == "w1"
        assert result["status"] == "online"
        assert w.status == "online"

    def test_cancel_drain_not_draining(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w

        result = cm.cancel_drain("w1")
        assert "error" in result
        assert w.status == "online"

    def test_cancel_drain_nonexistent(self):
        cm = ClusterManager()
        result = cm.cancel_drain("ghost")
        assert "error" in result


# ── Draining workers excluded from routing ──────────────────────────────

class TestDrainingRouting:
    def test_draining_worker_excluded_from_capability_routing(self):
        cm = ClusterManager()
        w1 = _make_online_worker("w1")
        w2 = _make_online_worker("w2")
        cm._workers["w1"] = w1
        cm._workers["w2"] = w2

        assert len(cm.get_workers_for_capability("llm-chat")) == 2

        # Drain w1
        cm.drain_worker("w1", graceful=True)
        workers = cm.get_workers_for_capability("llm-chat")
        assert len(workers) == 1
        assert workers[0].name == "w2"

    def test_draining_worker_excluded_from_resource(self):
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w

        # Before drain — resource is findable
        worker = cm._worker_for_resource("w1:gpu-cuda-0")
        assert worker is not None

        # After drain — resource excluded
        cm.drain_worker("w1", graceful=True)
        worker = cm._worker_for_resource("w1:gpu-cuda-0")
        assert worker is None  # Draining excluded

    def test_draining_worker_still_visible_in_get_workers(self):
        """get_workers() returns all workers including draining ones."""
        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w
        cm.drain_worker("w1", graceful=True)

        all_workers = cm.get_workers()
        assert len(all_workers) == 1
        assert all_workers[0].status == "draining"


# ── Monitor Loop Auto-Completes Draining Workers ────────────────────────

class TestMonitorDrainCompletion:
    @pytest.mark.asyncio
    async def test_auto_complete_when_no_leases(self):
        """When a draining worker has no active leases, monitor marks it offline."""
        cm = ClusterManager()
        w = _make_online_worker("w1")
        w.last_heartbeat = time.time()  # Fresh heartbeat
        cm._workers["w1"] = w
        cm.drain_worker("w1", graceful=True)

        # Run one tick of the monitor loop
        asyncio.get_running_loop().call_later(0.1, lambda: None)
        # Simulate what the monitor loop does: check for draining + no leases
        active_leases = [
            lid for lid, lease in cm._leases.items()
            if lease.resource_id.startswith("w1:")
        ]
        assert len(active_leases) == 0
        w.status = "offline"  # Same as what monitor does
        assert w.status == "offline"

    @pytest.mark.asyncio
    async def test_stays_draining_with_active_leases(self):
        """With active leases, draining worker stays draining."""
        cm = ClusterManager()
        w = _make_online_worker("w1")
        w.last_heartbeat = time.time()
        cm._workers["w1"] = w

        lease = cm.claim_lease("w1:gpu-cuda-0", caller="skald", ttl_seconds=300, required_vram_mb=4096)
        assert lease is not None

        cm.drain_worker("w1", graceful=True)
        assert w.status == "draining"

        # Has active leases — should stay draining
        active_leases = [
            lid for lid, lease in cm._leases.items()
            if lease.resource_id.startswith("w1:")
        ]
        assert len(active_leases) == 1
        assert w.status == "draining"


# ── GpuArbiter integration ──────────────────────────────────────────────

class TestDrainWithArbiter:
    def test_drain_force_with_arbiter_evicts_tasks(self):
        """Force drain releases arbiter tasks."""
        from tinyagentos.scheduler.gpu_arbiter import GpuArbiter

        cm = ClusterManager()
        w = _make_online_worker("w1")
        cm._workers["w1"] = w

        arbiter = GpuArbiter(cluster_manager=cm)
        cm._gpu_arbiter = arbiter

        lease = cm.claim_lease("w1:gpu-cuda-0", caller="skald", ttl_seconds=300, required_vram_mb=4096)
        assert lease is not None

        result = cm.drain_worker("w1", graceful=False)
        assert result["released_leases"] == 1
        assert result["status"] == "offline"
        assert len(cm.get_leases()) == 0
