"""Tests for taOS #640: cluster persistence, split-brain, circuit breaker."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tinyagentos.cluster.failure_tracker import FailureTracker
from tinyagentos.cluster.manager import ClusterManager, HEARTBEAT_TIMEOUT
from tinyagentos.cluster.router import TaskRouter
from tinyagentos.cluster.worker_protocol import GpuLease, WorkerInfo
from tinyagentos.cluster.worker_registry_store import WorkerRegistryStore


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


# ── FailureTracker tests (Fix 3: circuit breaker) ──────────────────────

class TestFailureTracker:
    def test_not_tripped_by_default(self):
        ft = FailureTracker()
        assert not ft.is_tripped("w1")

    def test_trips_after_threshold(self):
        ft = FailureTracker(failure_threshold=3)
        ft.record_failure("w1")
        ft.record_failure("w1")
        assert not ft.is_tripped("w1")
        ft.record_failure("w1")
        assert ft.is_tripped("w1")

    def test_success_resets_circuit(self):
        ft = FailureTracker(failure_threshold=2)
        ft.record_failure("w1")
        ft.record_failure("w1")
        assert ft.is_tripped("w1")
        ft.record_success("w1")
        assert not ft.is_tripped("w1")

    def test_separate_per_worker(self):
        ft = FailureTracker(failure_threshold=2)
        ft.record_failure("w1")
        ft.record_failure("w1")
        ft.record_failure("w2")
        assert ft.is_tripped("w1")
        assert not ft.is_tripped("w2")

    def test_reset_worker(self):
        ft = FailureTracker(failure_threshold=1)
        ft.record_failure("w1")
        assert ft.is_tripped("w1")
        ft.reset("w1")
        assert not ft.is_tripped("w1")

    def test_reset_all(self):
        ft = FailureTracker(failure_threshold=1)
        ft.record_failure("w1")
        ft.record_failure("w2")
        ft.reset_all()
        assert not ft.is_tripped("w1")
        assert not ft.is_tripped("w2")

    def test_sliding_window_does_not_discard_recent_failures(self):
        """Failures at t=0, t=59, t=61 — the t=59 failure must still count.

        The old fixed-bucket approach anchored the window at the first failure
        time (t=0) and would reset the entire window at t=61, discarding the
        t=59 failure that was only 2 seconds old (count would drop to 1).
        A true sliding window keeps the t=59 and t=61 timestamps (count=2),
        while correctly pruning the t=0 timestamp (age=61 s > window=60 s).
        """
        clock = [0.0]

        class ClockedTracker(FailureTracker):
            def __init__(self):
                super().__init__(failure_threshold=2, window_seconds=60.0)

        ft = ClockedTracker()
        ft._time_func = lambda: clock[0]

        clock[0] = 0.0
        ft.record_failure("w1")
        assert not ft.is_tripped("w1")

        clock[0] = 59.0
        ft.record_failure("w1")
        assert ft.is_tripped("w1")

        clock[0] = 61.0
        ft.record_failure("w1")
        assert ft.is_tripped("w1")

    def test_sliding_window_prunes_stale_failures(self):
        """Failures older than window_seconds should be pruned on check."""
        clock = [0.0]

        class ClockedTracker(FailureTracker):
            def __init__(self):
                super().__init__(failure_threshold=3, window_seconds=60.0)

        ft = ClockedTracker()
        ft._time_func = lambda: clock[0]

        # t=0: first failure
        clock[0] = 0.0
        ft.record_failure("w1")

        # t=30: second failure
        clock[0] = 30.0
        ft.record_failure("w1")

        # t=100: first failure is 100 s old (> window) and should be pruned.
        #         Only the t=30 failure remains — 1 failure → not tripped.
        clock[0] = 100.0
        assert not ft.is_tripped("w1")


# ── WorkerRegistryStore tests (Fix 1: persistence) ─────────────────────

@pytest.mark.asyncio
class TestWorkerRegistryStore:
    async def test_upsert_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            store = WorkerRegistryStore(db_path)
            await store.init()

            info = {
                "name": "gpu-box",
                "url": "http://gpu:9000",
                "hardware": json.dumps({"gpu": {"model": "RTX 4090"}}),
                "backends": json.dumps([]),
                "models": json.dumps(["llama3"]),
                "available_models": json.dumps([]),
                "capabilities": json.dumps(["chat", "embed"]),
                "status": "online",
                "last_heartbeat": 1234567890.0,
                "registered_at": 1234567890.0,
                "load": 0.5,
                "platform": "linux",
                "tier_id": "x86-cuda-24gb",
                "potential_capabilities": json.dumps([]),
                "kv_cache_quant_support": json.dumps(["fp16"]),
                "kv_cache_quant_k_support": json.dumps(["fp16"]),
                "kv_cache_quant_v_support": json.dumps(["fp16"]),
                "kv_cache_quant_boundary_layer_protect": 0,
                "worker_url": "http://gpu:6970",
                "signing_key": b"secret123456789012345678901234567890",
                "tls_cert_provider": None,
                "host_lan_ip": "192.168.1.100",
                "storage_cap_bytes": 100_000_000,
                "storage_used_bytes": 50_000_000,
                "bytes_deduped_total": 10_000_000,
                "worker_lxc_image_version": "ubuntu/24.04/amd64",
                "degraded": 0,
                "degraded_reason": None,
                "free_vram_mb": 16000,
                "used_vram_mb": 8000,
            }
            await store.upsert_worker(info)

            rows = await store.load_all()
            assert len(rows) == 1
            assert rows[0]["name"] == "gpu-box"
            assert rows[0]["url"] == "http://gpu:9000"
            assert rows[0]["status"] == "online"
            assert json.loads(rows[0]["models"]) == ["llama3"]
            assert rows[0]["free_vram_mb"] == 16000
            assert rows[0]["signing_key"] == b"secret123456789012345678901234567890"

            await store.close()

    async def test_upsert_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            store = WorkerRegistryStore(db_path)
            await store.init()

            info1 = {
                "name": "gpu-box",
                "url": "http://gpu:9000",
                "hardware": json.dumps({}),
                "backends": json.dumps([]),
                "models": json.dumps(["llama3"]),
                "available_models": json.dumps([]),
                "capabilities": json.dumps(["chat"]),
                "status": "online",
                "last_heartbeat": 100.0,
                "registered_at": 100.0,
                "load": 0.0,
                "platform": "linux",
                "tier_id": "",
                "potential_capabilities": json.dumps([]),
                "kv_cache_quant_support": json.dumps(["fp16"]),
                "kv_cache_quant_k_support": json.dumps(["fp16"]),
                "kv_cache_quant_v_support": json.dumps(["fp16"]),
                "kv_cache_quant_boundary_layer_protect": 0,
                "worker_url": None,
                "signing_key": b"",
                "tls_cert_provider": None,
                "host_lan_ip": None,
                "storage_cap_bytes": 0,
                "storage_used_bytes": 0,
                "bytes_deduped_total": 0,
                "worker_lxc_image_version": None,
                "degraded": 0,
                "degraded_reason": None,
                "free_vram_mb": None,
                "used_vram_mb": None,
            }
            await store.upsert_worker(info1)

            info2 = {**info1, "status": "offline", "load": 0.9,
                     "free_vram_mb": 4000, "used_vram_mb": 20000}
            await store.upsert_worker(info2)

            rows = await store.load_all()
            assert len(rows) == 1
            assert rows[0]["status"] == "offline"
            assert rows[0]["load"] == 0.9
            assert rows[0]["free_vram_mb"] == 4000

            await store.close()

    async def test_remove_worker(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            store = WorkerRegistryStore(db_path)
            await store.init()

            info = {
                "name": "gpu-box",
                "url": "http://gpu:9000",
                "hardware": json.dumps({}),
                "backends": json.dumps([]),
                "models": json.dumps([]),
                "available_models": json.dumps([]),
                "capabilities": json.dumps(["chat"]),
                "status": "online",
                "last_heartbeat": 100.0,
                "registered_at": 100.0,
                "load": 0.0,
                "platform": "linux",
                "tier_id": "",
                "potential_capabilities": json.dumps([]),
                "kv_cache_quant_support": json.dumps(["fp16"]),
                "kv_cache_quant_k_support": json.dumps(["fp16"]),
                "kv_cache_quant_v_support": json.dumps(["fp16"]),
                "kv_cache_quant_boundary_layer_protect": 0,
                "worker_url": None,
                "signing_key": b"",
                "tls_cert_provider": None,
                "host_lan_ip": None,
                "storage_cap_bytes": 0,
                "storage_used_bytes": 0,
                "bytes_deduped_total": 0,
                "worker_lxc_image_version": None,
                "degraded": 0,
                "degraded_reason": None,
                "free_vram_mb": None,
                "used_vram_mb": None,
            }
            await store.upsert_worker(info)
            assert len(await store.load_all()) == 1
            await store.remove_worker("gpu-box")
            assert len(await store.load_all()) == 0

            await store.close()

    async def test_generation_counter(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            store = WorkerRegistryStore(db_path)
            await store.init()

            # First generation should be > 0
            assert await store.current_generation() > 0

            g1 = await store.increment_generation()
            g2 = await store.increment_generation()
            assert g2 == g1 + 1

            # Re-open — generation persists
            await store.close()
            store2 = WorkerRegistryStore(db_path)
            await store2.init()
            assert await store2.current_generation() == g2
            await store2.close()


# ── ClusterManager persistence tests (Fix 1 + Fix 2) ───────────────────

@pytest.mark.asyncio
class TestClusterManagerPersistence:
    """Tests for loading from store, persisting on register/heartbeat,
    and generation-based split-brain protection (taOS #640)."""

    async def _new_store(self, tmpdir) -> WorkerRegistryStore:
        db_path = tmpdir / "workers.db"
        store = WorkerRegistryStore(db_path)
        await store.init()
        return store

    async def test_loads_persisted_workers_on_start(self, tmp_path):
        store = await self._new_store(tmp_path)
        # Pre-populate a worker in the store
        await store.upsert_worker({
            "name": "gpu-box",
            "url": "http://gpu:9000",
            "hardware": json.dumps({}),
            "backends": json.dumps([]),
            "models": json.dumps([]),
            "available_models": json.dumps([]),
            "capabilities": json.dumps(["chat"]),
            "status": "online",
            "last_heartbeat": time.time(),
            "registered_at": time.time(),
            "load": 0.5,
            "platform": "linux",
            "tier_id": "",
            "potential_capabilities": json.dumps([]),
            "kv_cache_quant_support": json.dumps(["fp16"]),
            "kv_cache_quant_k_support": json.dumps(["fp16"]),
            "kv_cache_quant_v_support": json.dumps(["fp16"]),
            "kv_cache_quant_boundary_layer_protect": 0,
            "worker_url": None,
            "signing_key": b"",
            "tls_cert_provider": None,
            "host_lan_ip": None,
            "storage_cap_bytes": 0,
            "storage_used_bytes": 0,
            "bytes_deduped_total": 0,
            "worker_lxc_image_version": None,
            "degraded": 0,
            "degraded_reason": None,
            "free_vram_mb": None,
            "used_vram_mb": None,
        })

        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()

        workers = mgr.get_workers()
        assert len(workers) == 1
        assert workers[0].name == "gpu-box"
        # Loaded workers start as "stale" — they must re-heartbeat
        assert workers[0].status == "stale"
        assert workers[0].load == 0.5

        await mgr.stop()
        await store.close()

    async def test_stale_worker_excluded_from_routing(self, tmp_path):
        store = await self._new_store(tmp_path)
        await store.upsert_worker({
            "name": "gpu-box",
            "url": "http://gpu:9000",
            "hardware": json.dumps({}),
            "backends": json.dumps([]),
            "models": json.dumps([]),
            "available_models": json.dumps([]),
            "capabilities": json.dumps(["chat"]),
            "status": "online",
            "last_heartbeat": time.time(),
            "registered_at": time.time(),
            "load": 0.5,
            "platform": "linux",
            "tier_id": "",
            "potential_capabilities": json.dumps([]),
            "kv_cache_quant_support": json.dumps(["fp16"]),
            "kv_cache_quant_k_support": json.dumps(["fp16"]),
            "kv_cache_quant_v_support": json.dumps(["fp16"]),
            "kv_cache_quant_boundary_layer_protect": 0,
            "worker_url": None,
            "signing_key": b"",
            "tls_cert_provider": None,
            "host_lan_ip": None,
            "storage_cap_bytes": 0,
            "storage_used_bytes": 0,
            "bytes_deduped_total": 0,
            "worker_lxc_image_version": None,
            "degraded": 0,
            "degraded_reason": None,
            "free_vram_mb": None,
            "used_vram_mb": None,
        })

        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()

        # Stale workers are NOT "online" so get_workers_for_capability skips them
        result = mgr.get_workers_for_capability("chat")
        assert len(result) == 0

        await mgr.stop()
        await store.close()

    async def test_heartbeat_revives_stale_worker(self, tmp_path):
        store = await self._new_store(tmp_path)
        await store.upsert_worker({
            "name": "gpu-box",
            "url": "http://gpu:9000",
            "hardware": json.dumps({}),
            "backends": json.dumps([]),
            "models": json.dumps([]),
            "available_models": json.dumps([]),
            "capabilities": json.dumps(["chat"]),
            "status": "online",
            "last_heartbeat": time.time(),
            "registered_at": time.time(),
            "load": 0.5,
            "platform": "linux",
            "tier_id": "",
            "potential_capabilities": json.dumps([]),
            "kv_cache_quant_support": json.dumps(["fp16"]),
            "kv_cache_quant_k_support": json.dumps(["fp16"]),
            "kv_cache_quant_v_support": json.dumps(["fp16"]),
            "kv_cache_quant_boundary_layer_protect": 0,
            "worker_url": None,
            "signing_key": b"",
            "tls_cert_provider": None,
            "host_lan_ip": None,
            "storage_cap_bytes": 0,
            "storage_used_bytes": 0,
            "bytes_deduped_total": 0,
            "worker_lxc_image_version": None,
            "degraded": 0,
            "degraded_reason": None,
            "free_vram_mb": None,
            "used_vram_mb": None,
        })

        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()
        assert mgr.get_worker("gpu-box").status == "stale"

        # Heartbeat revives from stale → online
        mgr.heartbeat("gpu-box", load=0.1, generation=mgr.generation)
        assert mgr.get_worker("gpu-box").status == "online"
        assert mgr.get_worker("gpu-box").load == 0.1

        await mgr.stop()
        await store.close()

    async def test_register_worker_persists(self, tmp_path):
        store = await self._new_store(tmp_path)
        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()

        w = _make_worker("gpu-box", capabilities=["chat"])
        await mgr.register_worker(w)

        # Check the store has the worker
        rows = await store.load_all()
        assert len(rows) == 1
        assert rows[0]["name"] == "gpu-box"
        assert rows[0]["status"] == "online"

        await mgr.stop()
        await store.close()

    async def test_unregister_worker_removes_from_store(self, tmp_path):
        store = await self._new_store(tmp_path)
        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()

        w = _make_worker("gpu-box", capabilities=["chat"])
        await mgr.register_worker(w)
        assert len(await store.load_all()) == 1

        await mgr.unregister_worker("gpu-box")
        assert len(await store.load_all()) == 0

        await mgr.stop()
        await store.close()

    async def test_generation_increments_on_start(self, tmp_path):
        store = await self._new_store(tmp_path)
        mgr1 = ClusterManager(worker_registry_store=store)
        await mgr1.start()
        g1 = mgr1.generation
        await mgr1.stop()

        # Create a fresh store connection so generation persists
        db_path = tmp_path / "workers.db"
        store2 = WorkerRegistryStore(db_path)
        await store2.init()
        mgr2 = ClusterManager(worker_registry_store=store2)
        await mgr2.start()
        g2 = mgr2.generation
        assert g2 == g1 + 1
        await mgr2.stop()
        await store2.close()
        await store.close()

    async def test_heartbeat_rejected_for_stale_generation(self, tmp_path):
        """Split-brain: heartbeat with wrong generation is rejected."""
        store = await self._new_store(tmp_path)
        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()

        w = _make_worker("gpu-box", capabilities=["chat"])
        await mgr.register_worker(w)
        current_gen = mgr.generation

        # Heartbeat with a different generation — rejected
        ok = mgr.heartbeat("gpu-box", load=0.1, generation=current_gen + 5)
        assert ok is False

        # Worker status unchanged
        assert mgr.get_worker("gpu-box").status == "online"

        await mgr.stop()
        await store.close()

    async def test_heartbeat_accepted_with_null_generation(self, tmp_path):
        """Legacy workers that don't send generation are accepted."""
        store = await self._new_store(tmp_path)
        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()

        w = _make_worker("gpu-box", capabilities=["chat"])
        await mgr.register_worker(w)

        ok = mgr.heartbeat("gpu-box", load=0.1)  # no generation
        assert ok is True

        await mgr.stop()
        await store.close()

    async def test_local_worker_not_loaded_from_store(self, tmp_path):
        """The 'local' worker is skipped when loading from store."""
        store = await self._new_store(tmp_path)
        # Put a "local" worker in the store
        await store.upsert_worker({
            "name": "local",
            "url": "http://local:9000",
            "hardware": json.dumps({}),
            "backends": json.dumps([]),
            "models": json.dumps([]),
            "available_models": json.dumps([]),
            "capabilities": json.dumps(["chat"]),
            "status": "online",
            "last_heartbeat": time.time(),
            "registered_at": time.time(),
            "load": 0.0,
            "platform": "linux",
            "tier_id": "",
            "potential_capabilities": json.dumps([]),
            "kv_cache_quant_support": json.dumps(["fp16"]),
            "kv_cache_quant_k_support": json.dumps(["fp16"]),
            "kv_cache_quant_v_support": json.dumps(["fp16"]),
            "kv_cache_quant_boundary_layer_protect": 0,
            "worker_url": None,
            "signing_key": b"",
            "tls_cert_provider": None,
            "host_lan_ip": None,
            "storage_cap_bytes": 0,
            "storage_used_bytes": 0,
            "bytes_deduped_total": 0,
            "worker_lxc_image_version": None,
            "degraded": 0,
            "degraded_reason": None,
            "free_vram_mb": None,
            "used_vram_mb": None,
        })

        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()

        # "local" should NOT be loaded from store
        assert mgr.get_worker("local") is None

        await mgr.stop()
        await store.close()

    async def test_generation_property_without_store(self):
        """When no store is wired, generation defaults to 1 and stays stable."""
        mgr = ClusterManager()  # no store
        await mgr.start()
        assert mgr.generation == 1
        await mgr.stop()


    async def test_controller_fenced_when_generation_advanced(self, tmp_path):
        """Controller A at gen 2 fences itself when controller B advances to gen 3."""
        store = await self._new_store(tmp_path)
        mgr_a = ClusterManager(worker_registry_store=store)
        await mgr_a.start()
        assert mgr_a.generation >= 1
        gen_a = mgr_a.generation

        # Register a worker so we can verify it gets fenced
        w = _make_worker("gpu-box", capabilities=["chat"])
        await mgr_a.register_worker(w)
        assert mgr_a.get_worker("gpu-box").status == "online"

        # Controller B advances the durable generation past A's
        await store.increment_generation()
        durable_gen = await store.current_generation()
        assert durable_gen > gen_a

        # Simulate the monitor loop's fence detection
        mgr_a._fenced = False  # reset just in case
        durable_check = await store.current_generation()
        if durable_check > mgr_a._generation:
            mgr_a._fenced = True
            for worker in list(mgr_a._workers.values()):
                if worker.name != "local" and worker.status in ("online", "update-available"):
                    worker.status = "offline"

        assert mgr_a._fenced is True
        assert mgr_a.get_worker("gpu-box").status == "offline"

        # After fencing, heartbeat should be rejected
        ok = mgr_a.heartbeat("gpu-box", load=0.1, generation=gen_a)
        assert ok is False

        # After fencing, new registration should be silently rejected
        w2 = _make_worker("new-worker", capabilities=["chat"])
        await mgr_a.register_worker(w2)
        assert mgr_a.get_worker("new-worker") is None

        await mgr_a.stop()
        await store.close()

    async def test_fenced_controller_releases_leases_and_cancels_arbiter(self, tmp_path):
        """A fenced controller must release GPU leases and cancel in-flight
        arbiter tasks for its workers, mirroring the sibling termination
        branches. Driven by the REAL monitor loop (not a re-implemented check)."""
        store = await self._new_store(tmp_path)
        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()
        assert mgr.generation >= 1

        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))
        assert mgr.get_worker("gpu-box").status == "online"

        lease_id = "l_gpu_box_0"
        mgr._leases[lease_id] = GpuLease(
            lease_id=lease_id,
            resource_id="gpu-box:gpu-cuda-0",
            caller="test-caller",
            expires_at=time.time() + 3600,
            required_vram_mb=1024,
        )

        cancel_mock = AsyncMock()
        cancel_mock.cancel_running_for_leases.return_value = (1, 0)
        mgr._gpu_arbiter = cancel_mock

        await store.increment_generation()
        durable_gen = await store.current_generation()
        assert durable_gen > mgr.generation

        # start() already spawned a _monitor_loop task; cancel it so only
        # the loop instance below runs, else both can enter the fenced
        # branch and double-call the arbiter cancel.
        mgr._monitor_task.cancel()
        try:
            await mgr._monitor_task
        except asyncio.CancelledError:
            pass

        task = asyncio.create_task(mgr._monitor_loop())
        try:
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert mgr.get_worker("gpu-box").status == "offline"
        assert lease_id not in mgr._leases
        cancel_mock.cancel_running_for_leases.assert_called_once_with({lease_id})

        await mgr.stop()
        await store.close()

    async def test_fenced_controller_releases_leases_of_draining_worker(self, tmp_path):
        """Fencing must release leases for EVERY non-local worker, including
        one mid-drain: the fenced branch `continue`s past the normal
        stale-drain cleanup, so anything it skips here leaks forever."""
        store = await self._new_store(tmp_path)
        mgr = ClusterManager(worker_registry_store=store)
        await mgr.start()
        assert mgr.generation >= 1

        await mgr.register_worker(_make_worker("gpu-box", capabilities=["chat"]))
        mgr.get_worker("gpu-box").status = "draining"

        lease_id = "l_gpu_box_drain_0"
        mgr._leases[lease_id] = GpuLease(
            lease_id=lease_id,
            resource_id="gpu-box:gpu-cuda-0",
            caller="test-caller",
            expires_at=time.time() + 3600,
            required_vram_mb=1024,
        )

        cancel_mock = AsyncMock()
        cancel_mock.cancel_running_for_leases.return_value = (1, 0)
        mgr._gpu_arbiter = cancel_mock

        await store.increment_generation()
        assert await store.current_generation() > mgr.generation

        mgr._monitor_task.cancel()
        try:
            await mgr._monitor_task
        except asyncio.CancelledError:
            pass

        task = asyncio.create_task(mgr._monitor_loop())
        try:
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Status stays draining (fencing only transitions online/
        # update-available workers), but the lease must be gone and the
        # arbiter cancel must have fired.
        assert mgr.get_worker("gpu-box").status == "draining"
        assert lease_id not in mgr._leases
        cancel_mock.cancel_running_for_leases.assert_called_once_with({lease_id})

        await mgr.stop()
        await store.close()


# ── TaskRouter circuit breaker tests (Fix 3) ───────────────────────────

@pytest.mark.asyncio
class TestTaskRouterCircuitBreaker:
    async def test_skips_tripped_worker(self):
        ft = FailureTracker(failure_threshold=2)
        mgr = ClusterManager(failure_tracker=ft)
        await mgr.register_worker(_make_worker("w1", capabilities=["chat"], load=0.1, url="http://w1:8000"))
        await mgr.register_worker(_make_worker("w2", capabilities=["chat"], load=0.5, url="http://w2:8000"))

        # Trip w1
        ft.record_failure("w1")
        ft.record_failure("w1")
        assert ft.is_tripped("w1")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        ok_resp.json.return_value = {"result": "ok"}
        # Only w2 should be tried (w1 skipped)
        mock_client.post.return_value = ok_resp

        router = TaskRouter(mgr, mock_client)
        data, name = await router.route_request("chat", "POST", "/v1/chat/completions", {})

        assert data == {"result": "ok"}
        assert name == "w2"
        # Only 1 call was made (w1 was skipped, w2 succeeded)
        assert mock_client.post.call_count == 1

    async def test_records_failure_on_error(self):
        ft = FailureTracker(failure_threshold=1)
        mgr = ClusterManager(failure_tracker=ft)
        await mgr.register_worker(_make_worker("w1", capabilities=["chat"], load=0.1, url="http://w1:8000"))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = Exception("connection refused")

        router = TaskRouter(mgr, mock_client)
        data, name = await router.route_request("chat", "POST", "/v1/chat/completions", {})

        assert data is None
        assert ft.is_tripped("w1") is True  # 1 failure, threshold=1 → tripped

    async def test_clears_failure_on_success(self):
        ft = FailureTracker(failure_threshold=2)
        mgr = ClusterManager(failure_tracker=ft)
        await mgr.register_worker(_make_worker("w1", capabilities=["chat"], load=0.1, url="http://w1:8000"))

        # Record one failure — threshold is 2 so not tripped yet.
        ft.record_failure("w1")
        assert not ft.is_tripped("w1")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        ok_resp.json.return_value = {"result": "ok"}
        mock_client.post.return_value = ok_resp

        router = TaskRouter(mgr, mock_client)
        data, name = await router.route_request("chat", "POST", "/v1/chat/completions", {})
        assert data == {"result": "ok"}
        # After success, circuit should be reset
        assert not ft.is_tripped("w1")

        # Record another failure after success — should NOT be tripped
        # because the success cleared the previous failure counter.
        # If the circuit was NOT cleared, this would be failure #2 → tripped.
        ft.record_failure("w1")
        assert not ft.is_tripped("w1")

    async def test_router_without_tracker_still_works(self):
        """Router works fine when no failure_tracker is wired."""
        mgr = ClusterManager()  # no failure_tracker
        await mgr.register_worker(_make_worker("w1", capabilities=["chat"], load=0.1, url="http://w1:8000"))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        fail_resp = MagicMock()
        fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        ok_resp.json.return_value = {"result": "ok"}
        mock_client.post.side_effect = [fail_resp, ok_resp]

        # Add a second worker so the first failure doesn't exhaust all workers
        await mgr.register_worker(_make_worker("w2", capabilities=["chat"], load=0.5, url="http://w2:8000"))

        router = TaskRouter(mgr, mock_client)
        data, name = await router.route_request("chat", "POST", "/v1/chat/completions", {})

        assert data == {"result": "ok"}
        assert name == "w2"
        assert mock_client.post.call_count == 2

    async def test_cooldown_readmits_worker_after_window(self):
        """End-to-end circuit breaker cooldown recovery (taOS #640 Fix 3).

        Driven entirely through ``route_request`` (the real caller), NOT by
        poking FailureTracker directly:

          1. breaker trips — two 5xx failures (threshold=2) trip w1.
          2. worker excluded — route_request skips w1, makes no HTTP call,
             returns (None, None) because there are no healthy fallbacks.
          3. window elapses — fake clock advances past window_seconds.
          4. router re-admits — route_request retries w1, gets a 200, clears
             the circuit, and returns the response + worker name.

        Complements ``test_clears_failure_on_success`` which only proves
        record_success clears immediately. This proves the *time-based*
        sliding-window recovery lets the router re-attempt a previously
        excluded worker.
        """
        clock = [1000.0]

        ft = FailureTracker(failure_threshold=2, window_seconds=60.0)
        ft._time_func = lambda: clock[0]

        mgr = ClusterManager(failure_tracker=ft)
        await mgr.register_worker(
            _make_worker("w1", capabilities=["chat"], load=0.1, url="http://w1:8000")
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        router = TaskRouter(mgr, mock_client)

        def _fail_500():
            resp = MagicMock()
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock(status_code=500)
            )
            return resp

        def _ok_200():
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"result": "ok"}
            return resp

        # Phase 1 — trip the breaker via real route_request failures.
        mock_client.post.side_effect = [_fail_500(), _fail_500()]
        await router.route_request("chat", "POST", "/v1/chat/completions", {})
        await router.route_request("chat", "POST", "/v1/chat/completions", {})
        assert ft.is_tripped("w1")
        assert mock_client.post.call_count == 2

        # Phase 2 — worker is excluded: route_request skips w1 entirely
        # (no HTTP call) and returns (None, None) because w1 is the only worker.
        calls_before = mock_client.post.call_count
        data, name = await router.route_request("chat", "POST", "/v1/chat/completions", {})
        assert data is None
        assert name is None
        assert mock_client.post.call_count == calls_before

        # Phase 3 — window elapses via fake clock (no sleep).
        clock[0] += 61.0

        # Phase 4 — router re-attempts w1 and re-admits it.
        mock_client.post.side_effect = [_ok_200()]
        data, name = await router.route_request("chat", "POST", "/v1/chat/completions", {})
        assert data == {"result": "ok"}
        assert name == "w1"
        assert mock_client.post.call_count == calls_before + 1
        assert not ft.is_tripped("w1")
