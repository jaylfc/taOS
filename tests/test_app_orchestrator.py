import pytest
from tinyagentos.app_orchestrator import AppOrchestrator
from tinyagentos.streaming import StreamingSessionStore
from unittest.mock import MagicMock


class TestAppOrchestrator:
    @pytest.mark.asyncio
    async def test_pick_worker_local_by_default(self, tmp_path):
        cluster = MagicMock()
        cluster.get_workers.return_value = []
        store = StreamingSessionStore(tmp_path / "s.db")
        await store.init()
        try:
            orch = AppOrchestrator(cluster, store, None)
            worker = orch.pick_worker({})
            assert worker == "local"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_pick_worker_gpu_preference(self, tmp_path):
        gpu_worker = MagicMock()
        gpu_worker.name = "gpu-pc"
        gpu_worker.status = "online"
        gpu_worker.hardware = {"gpu": {"type": "nvidia", "vram_mb": 8192}}

        cluster = MagicMock()
        cluster.get_workers.return_value = [gpu_worker]

        store = StreamingSessionStore(tmp_path / "s.db")
        await store.init()
        try:
            orch = AppOrchestrator(cluster, store, None)
            worker = orch.pick_worker({"requires": {"gpu_recommended": True}})
            assert worker == "gpu-pc"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_launch_creates_session(self, tmp_path):
        cluster = MagicMock()
        cluster.get_workers.return_value = []
        store = StreamingSessionStore(tmp_path / "s.db")
        await store.init()
        try:
            orch = AppOrchestrator(cluster, store, None)
            result = await orch.launch("blender", {}, "blender-expert")
            assert result["session_id"] is not None
            assert result["status"] == "running"
            assert result["worker_name"] == "local"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_stop_session(self, tmp_path):
        cluster = MagicMock()
        cluster.get_workers.return_value = []
        store = StreamingSessionStore(tmp_path / "s.db")
        await store.init()
        try:
            orch = AppOrchestrator(cluster, store, None)
            result = await orch.launch("blender", {}, "expert")
            stop_result = await orch.stop(result["session_id"])
            assert stop_result["status"] == "stopped"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_stop_nonexistent(self, tmp_path):
        cluster = MagicMock()
        store = StreamingSessionStore(tmp_path / "s.db")
        await store.init()
        try:
            orch = AppOrchestrator(cluster, store, None)
            result = await orch.stop("nonexistent")
            assert "error" in result
        finally:
            await store.close()
