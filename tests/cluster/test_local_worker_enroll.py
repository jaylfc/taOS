"""Task 23: Tests for enroll_local_worker + worker_registry bridge."""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.cluster.manager import ClusterManager



class TestEnrollLocalWorker:
    """enroll_local_worker registers a 'local' worker in the ClusterManager."""

    def test_enroll_creates_local_worker(self):
        from tinyagentos.cluster.local_worker import enroll_local_worker

        mgr = ClusterManager()
        asyncio.run(enroll_local_worker(mgr))
        worker = mgr.get_worker("local")
        assert worker is not None
        assert worker.name == "local"

    def test_enroll_sets_worker_url(self):
        from tinyagentos.cluster.local_worker import enroll_local_worker

        mgr = ClusterManager()
        asyncio.run(enroll_local_worker(mgr, bind_port=7777))
        worker = mgr.get_worker("local")
        assert worker.worker_url == "http://127.0.0.1:7777"

    def test_enroll_default_port_is_6969(self):
        from tinyagentos.cluster.local_worker import enroll_local_worker

        mgr = ClusterManager()
        asyncio.run(enroll_local_worker(mgr))
        worker = mgr.get_worker("local")
        assert worker.worker_url == "http://127.0.0.1:6969"

    def test_enroll_generates_random_signing_key(self):
        import tinyagentos.cluster.local_worker as lw
        from tinyagentos.cluster.local_worker import enroll_local_worker

        # Reset the module key so each manager gets a freshly generated key.
        lw._LOCAL_SIGNING_KEY = None
        mgr1 = ClusterManager()
        asyncio.run(enroll_local_worker(mgr1))
        key1 = mgr1.get_worker("local").signing_key

        lw._LOCAL_SIGNING_KEY = None
        mgr2 = ClusterManager()
        asyncio.run(enroll_local_worker(mgr2))
        key2 = mgr2.get_worker("local").signing_key

        # 32 random bytes — should almost certainly differ across independent runs
        assert len(key1) == 32
        assert len(key2) == 32
        assert key1 != key2

    def test_enroll_signing_key_is_bytes(self):
        from tinyagentos.cluster.local_worker import enroll_local_worker

        mgr = ClusterManager()
        asyncio.run(enroll_local_worker(mgr))
        key = mgr.get_worker("local").signing_key
        assert isinstance(key, bytes)

    def test_enroll_is_idempotent_same_manager(self):
        """Calling twice on the same manager keeps the same signing_key."""
        from tinyagentos.cluster.local_worker import enroll_local_worker

        mgr = ClusterManager()
        asyncio.run(enroll_local_worker(mgr))
        first_key = mgr.get_worker("local").signing_key

        asyncio.run(enroll_local_worker(mgr))
        second_key = mgr.get_worker("local").signing_key

        assert first_key == second_key  # idempotent: key stable across calls


class TestWorkerRegistryBridge:
    """get_local_worker raises RuntimeError when no manager is active (fail closed)."""

    def test_no_manager_raises_runtime_error(self):
        import tinyagentos.cluster.worker_registry as wr

        # Reset active manager to verify fail-closed behaviour
        original = wr._active_manager
        wr._active_manager = None
        try:
            with pytest.raises(RuntimeError, match="No active ClusterManager"):
                wr.get_local_worker()
        finally:
            wr._active_manager = original

    def test_manager_without_local_worker_raises_runtime_error(self):
        import tinyagentos.cluster.worker_registry as wr

        original = wr._active_manager
        empty_mgr = ClusterManager()
        wr.set_active_manager(empty_mgr)
        try:
            with pytest.raises(RuntimeError, match="Local worker not registered"):
                wr.get_local_worker()
        finally:
            wr._active_manager = original

    def test_set_active_manager_makes_get_local_worker_use_manager(self):
        import tinyagentos.cluster.worker_registry as wr
        from tinyagentos.cluster.local_worker import enroll_local_worker

        mgr = ClusterManager()
        asyncio.run(enroll_local_worker(mgr, bind_port=6969))
        original = wr._active_manager
        wr.set_active_manager(mgr)
        try:
            result = wr.get_local_worker()
            worker = mgr.get_worker("local")
            assert result["signing_key"] == worker.signing_key
            assert result["name"] == "local"
        finally:
            wr._active_manager = original
