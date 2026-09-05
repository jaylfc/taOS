"""Unit tests for tinyagentos.scheduling.resource_manager.

Covers:
- ResourceSnapshot properties and to_dict
- _count_cpu_cores, _detect_npu, _detect_gpu, _get_available_ram_mb
- _check_ollama_models, _check_cluster_workers
- ResourceManager: refresh, get_snapshot, yield/reclaim, best_model_for_task,
  evaluate_migration, can_accept_job, _model_fits_in_ram
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.scheduling.resource_manager import (
    ResourceSnapshot,
    _check_cluster_workers,
    _check_ollama_models,
    _count_cpu_cores,
    _detect_gpu,
    _detect_npu,
    _get_available_ram_mb,
)


# ---------------------------------------------------------------------------
# ResourceSnapshot
# ---------------------------------------------------------------------------


class TestResourceSnapshot:
    def test_to_dict_returns_expected_keys(self):
        snap = ResourceSnapshot()
        snap.cpu_cores = 4
        snap.npu_cores = 3
        snap.gpu = {"name": "RTX 4090", "vram_mb": 24576, "count": 1}
        snap.ram_available_mb = 8192
        snap.ollama_models = ["qwen3.5:4b"]
        snap.cluster_workers = [{"name": "w1"}]

        d = snap.to_dict()
        assert d == {
            "timestamp": snap.timestamp,
            "cpu_cores": 4,
            "npu_cores": 3,
            "gpu": {"name": "RTX 4090", "vram_mb": 24576, "count": 1},
            "ram_available_mb": 8192,
            "ollama_models": ["qwen3.5:4b"],
            "cluster_workers": 1,
        }

    def test_has_gpu_true_when_gpu_dict_populated(self):
        snap = ResourceSnapshot()
        snap.gpu = {"name": "RTX 4090", "vram_mb": 24576, "count": 1}
        assert snap.has_gpu is True

    def test_has_gpu_false_when_gpu_dict_empty(self):
        snap = ResourceSnapshot()
        assert snap.has_gpu is False

    def test_has_npu_true_when_npu_cores_positive(self):
        snap = ResourceSnapshot()
        snap.npu_cores = 3
        assert snap.has_npu is True

    def test_has_npu_false_when_npu_cores_zero(self):
        snap = ResourceSnapshot()
        assert snap.has_npu is False

    def test_has_ollama_true_when_models_present(self):
        snap = ResourceSnapshot()
        snap.ollama_models = ["qwen3.5:4b"]
        assert snap.has_ollama is True

    def test_has_ollama_false_when_models_empty(self):
        snap = ResourceSnapshot()
        assert snap.has_ollama is False

    def test_total_gpu_workers_counts_only_gpu_workers(self):
        snap = ResourceSnapshot()
        snap.cluster_workers = [
            {"name": "w1", "gpu": True},
            {"name": "w2", "gpu": False},
            {"name": "w3", "gpu": True},
        ]
        assert snap.total_gpu_workers == 2

    def test_total_gpu_workers_empty_cluster(self):
        snap = ResourceSnapshot()
        assert snap.total_gpu_workers == 0


# ---------------------------------------------------------------------------
# Module-level probe functions
# ---------------------------------------------------------------------------


class TestCountCpuCores:
    def test_returns_cpu_count(self):
        with patch("tinyagentos.scheduling.resource_manager.os.cpu_count", return_value=8):
            assert _count_cpu_cores() == 8

    def test_fallback_when_cpu_count_is_none(self):
        with patch("tinyagentos.scheduling.resource_manager.os.cpu_count", return_value=None):
            assert _count_cpu_cores() == 2

    def test_fallback_when_cpu_count_raises(self):
        with patch("tinyagentos.scheduling.resource_manager.os.cpu_count", side_effect=OSError):
            assert _count_cpu_cores() == 2


class TestDetectNpu:
    def test_returns_3_when_rknn_path_exists(self, tmp_path: Path):
        rknn = tmp_path / "npu"
        rknn.mkdir()
        with patch("tinyagentos.scheduling.resource_manager.Path", side_effect=lambda p: rknn if p == "/proc/device-tree/npu" else Path(p)):
            assert _detect_npu() == 3

    def test_returns_0_when_no_npu_paths(self):
        fake_paths = {
            "/proc/device-tree/npu": Path("/nonexistent/npu"),
            "/sys/class/misc/mali0": Path("/nonexistent/mali0"),
        }
        with patch("tinyagentos.scheduling.resource_manager.Path", side_effect=lambda p: fake_paths.get(p, Path(p))):
            assert _detect_npu() == 0


class TestDetectGpu:
    def test_parses_nvidia_smi_output(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "RTX 4090, 24576, 1\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = _detect_gpu()
        assert result == {"name": "RTX 4090", "vram_mb": 24576, "count": 1}
        mock_run.assert_called_once()

    def test_returns_empty_when_nvidia_smi_fails(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert _detect_gpu() == {}

    def test_returns_empty_on_exception(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _detect_gpu() == {}


class TestGetAvailableRamMb:
    def test_parses_meminfo(self):
        meminfo = "MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n"
        mock_open = MagicMock()
        mock_open.__enter__ = MagicMock(return_value=meminfo.splitlines())
        mock_open.__exit__ = MagicMock(return_value=False)
        with patch("builtins.open", return_value=mock_open):
            assert _get_available_ram_mb() == 8000

    def test_returns_zero_on_exception(self):
        with patch("builtins.open", side_effect=PermissionError):
            assert _get_available_ram_mb() == 0


class TestCheckOllamaModels:
    @pytest.mark.asyncio
    async def test_returns_model_names_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen3.5:4b"}, {"name": "qwen3.5:0.8b"}]}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _check_ollama_models("http://localhost:11434")
        assert result == ["qwen3.5:4b", "qwen3.5:0.8b"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _check_ollama_models()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("httpx.AsyncClient", side_effect=ConnectionError):
            result = await _check_ollama_models()
        assert result == []


class TestCheckClusterWorkers:
    @pytest.mark.asyncio
    async def test_returns_workers_when_controller_url_provided(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"workers": [{"name": "w1", "gpu": True}]}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _check_cluster_workers("http://controller:8080")
        assert result == [{"name": "w1", "gpu": True}]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_controller_url(self):
        result = await _check_cluster_workers("")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("httpx.AsyncClient", side_effect=ConnectionError):
            result = await _check_cluster_workers("http://controller:8080")
        assert result == []


# ---------------------------------------------------------------------------
# ResourceManager
# ---------------------------------------------------------------------------


def _make_snapshot(
    *,
    cpu_cores: int = 4,
    npu_cores: int = 0,
    gpu: dict | None = None,
    ram_available_mb: int = 8192,
    ollama_models: list[str] | None = None,
    cluster_workers: list[dict] | None = None,
    timestamp: float | None = None,
) -> ResourceSnapshot:
    snap = ResourceSnapshot()
    snap.cpu_cores = cpu_cores
    snap.npu_cores = npu_cores
    snap.gpu = gpu or {}
    snap.ram_available_mb = ram_available_mb
    snap.ollama_models = ollama_models or []
    snap.cluster_workers = cluster_workers or []
    if timestamp is not None:
        snap.timestamp = timestamp
    return snap


class TestResourceManager:
    def test_is_yielded_defaults_false(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        assert mgr.is_yielded is False

    @pytest.mark.asyncio
    async def test_yield_resources_sets_flag_and_throttles_queue(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        mgr = ResourceManager(job_queue=queue)

        result = await mgr.yield_resources()

        assert mgr.is_yielded is True
        assert result == {"mode": "yielded", "cpu": 1, "gpu": 0, "npu": 0}
        queue.set_limit.assert_any_call("cpu", 1)
        queue.set_limit.assert_any_call("gpu", 0)
        queue.set_limit.assert_any_call("npu", 0)
        queue.set_limit.assert_any_call("embed", 1)

    @pytest.mark.asyncio
    async def test_yield_resources_without_queue_does_not_error(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        result = await mgr.yield_resources()
        assert result["mode"] == "yielded"

    @pytest.mark.asyncio
    async def test_reclaim_resources_clears_flag_and_refreshes(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        queue.get_limits.return_value = {"cpu": 4, "gpu": 1}
        mgr = ResourceManager(job_queue=queue)

        fake_snap = _make_snapshot()
        with patch.object(mgr, "refresh", new_callable=AsyncMock, return_value=fake_snap):
            result = await mgr.reclaim_resources()

        assert mgr.is_yielded is False
        assert result["mode"] == "full"
        assert result["cpu"] == 4
        assert result["gpu"] == 1

    @pytest.mark.asyncio
    async def test_refresh_probes_all_resources_and_updates_snapshot(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        mgr = ResourceManager(job_queue=queue, ollama_url="http://ollama:11434")

        fake_snap = _make_snapshot(
            cpu_cores=8,
            npu_cores=3,
            gpu={"name": "RTX 4090", "vram_mb": 24576, "count": 1},
            ram_available_mb=16384,
            ollama_models=["qwen3.5:4b"],
            cluster_workers=[{"name": "w1"}],
        )

        with (
            patch("tinyagentos.scheduling.resource_manager._count_cpu_cores", return_value=8),
            patch("tinyagentos.scheduling.resource_manager._detect_npu", return_value=3),
            patch("tinyagentos.scheduling.resource_manager._detect_gpu", return_value={"name": "RTX 4090", "vram_mb": 24576, "count": 1}),
            patch("tinyagentos.scheduling.resource_manager._get_available_ram_mb", return_value=16384),
            patch("tinyagentos.scheduling.resource_manager._check_ollama_models", new_callable=AsyncMock, return_value=["qwen3.5:4b"]),
            patch("tinyagentos.scheduling.resource_manager._check_cluster_workers", new_callable=AsyncMock, return_value=[{"name": "w1"}]),
            patch("time.time", return_value=1000.0),
        ):
            snap = await mgr.refresh()

        assert mgr._snapshot is snap
        assert mgr._last_refresh == 1000.0
        assert snap.cpu_cores == 8
        assert snap.npu_cores == 3
        assert snap.gpu == {"name": "RTX 4090", "vram_mb": 24576, "count": 1}
        assert snap.ram_available_mb == 16384
        assert snap.ollama_models == ["qwen3.5:4b"]
        assert snap.cluster_workers == [{"name": "w1"}]

    @pytest.mark.asyncio
    async def test_refresh_applies_limits_via_queue(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        mgr = ResourceManager(job_queue=queue)

        with (
            patch("tinyagentos.scheduling.resource_manager._count_cpu_cores", return_value=4),
            patch("tinyagentos.scheduling.resource_manager._detect_npu", return_value=0),
            patch("tinyagentos.scheduling.resource_manager._detect_gpu", return_value={}),
            patch("tinyagentos.scheduling.resource_manager._get_available_ram_mb", return_value=8192),
            patch("tinyagentos.scheduling.resource_manager._check_ollama_models", new_callable=AsyncMock, return_value=[]),
            patch("tinyagentos.scheduling.resource_manager._check_cluster_workers", new_callable=AsyncMock, return_value=[]),
            patch("time.time", return_value=1000.0),
        ):
            await mgr.refresh()

        queue.set_limit.assert_any_call("cpu", 4)
        queue.set_limit.assert_any_call("npu", 0)
        queue.set_limit.assert_any_call("gpu", 0)
        queue.set_limit.assert_any_call("embed", 1)

    @pytest.mark.asyncio
    async def test_refresh_skips_limits_when_yielded(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        mgr = ResourceManager(job_queue=queue)
        mgr._yielded = True

        with (
            patch("tinyagentos.scheduling.resource_manager._count_cpu_cores", return_value=4),
            patch("tinyagentos.scheduling.resource_manager._detect_npu", return_value=0),
            patch("tinyagentos.scheduling.resource_manager._detect_gpu", return_value={}),
            patch("tinyagentos.scheduling.resource_manager._get_available_ram_mb", return_value=8192),
            patch("tinyagentos.scheduling.resource_manager._check_ollama_models", new_callable=AsyncMock, return_value=[]),
            patch("tinyagentos.scheduling.resource_manager._check_cluster_workers", new_callable=AsyncMock, return_value=[]),
            patch("time.time", return_value=1000.0),
        ):
            await mgr.refresh()

        queue.set_limit.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_uses_registry_over_controller(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        registry = AsyncMock()
        registry.for_resource_manager = AsyncMock(return_value=[{"name": "from_registry"}])
        mgr = ResourceManager(worker_registry=registry, controller_url="http://controller:8080")

        with (
            patch("tinyagentos.scheduling.resource_manager._count_cpu_cores", return_value=2),
            patch("tinyagentos.scheduling.resource_manager._detect_npu", return_value=0),
            patch("tinyagentos.scheduling.resource_manager._detect_gpu", return_value={}),
            patch("tinyagentos.scheduling.resource_manager._get_available_ram_mb", return_value=4096),
            patch("tinyagentos.scheduling.resource_manager._check_ollama_models", new_callable=AsyncMock, return_value=[]),
            patch("tinyagentos.scheduling.resource_manager._check_cluster_workers", new_callable=AsyncMock, return_value=[{"name": "from_controller"}]),
            patch("time.time", return_value=1000.0),
        ):
            snap = await mgr.refresh()

        assert snap.cluster_workers == [{"name": "from_registry"}]

    @pytest.mark.asyncio
    async def test_get_snapshot_returns_cached_when_fresh(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager(refresh_interval=60)
        cached = _make_snapshot()
        mgr._snapshot = cached
        mgr._last_refresh = time.time()

        snap = await mgr.get_snapshot()
        assert snap is cached

    @pytest.mark.asyncio
    async def test_get_snapshot_refreshes_when_stale(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager(refresh_interval=60)
        mgr._snapshot = _make_snapshot()
        mgr._last_refresh = time.time() - 120

        new_snap = _make_snapshot(cpu_cores=16)
        with patch.object(mgr, "refresh", new_callable=AsyncMock, return_value=new_snap):
            snap = await mgr.get_snapshot()
        assert snap.cpu_cores == 16

    @pytest.mark.asyncio
    async def test_get_snapshot_force_refresh(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager(refresh_interval=60)
        mgr._snapshot = _make_snapshot()
        mgr._last_refresh = time.time()

        new_snap = _make_snapshot(cpu_cores=16)
        with patch.object(mgr, "refresh", new_callable=AsyncMock, return_value=new_snap):
            snap = await mgr.get_snapshot(force_refresh=True)
        assert snap.cpu_cores == 16

    @pytest.mark.asyncio
    async def test_get_snapshot_refreshes_when_no_snapshot(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager(refresh_interval=60)
        new_snap = _make_snapshot(cpu_cores=4)
        with patch.object(mgr, "refresh", new_callable=AsyncMock, return_value=new_snap):
            snap = await mgr.get_snapshot()
        assert snap.cpu_cores == 4


# ---------------------------------------------------------------------------
# _model_fits_in_ram
# ---------------------------------------------------------------------------


class TestModelFitsInRam:
    def test_known_model_fits(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        assert mgr._model_fits_in_ram("qwen3.5:0.8b", 2000) is True

    def test_known_model_does_not_fit(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        assert mgr._model_fits_in_ram("qwen3.5:27b", 8000) is False

    def test_unknown_model_fits_with_lots_of_ram(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        assert mgr._model_fits_in_ram("custom-model", 5000) is True

    def test_unknown_model_does_not_fit_with_low_ram(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        assert mgr._model_fits_in_ram("custom-model", 3000) is False


# ---------------------------------------------------------------------------
# best_model_for_task
# ---------------------------------------------------------------------------


class TestBestModelForTask:
    @pytest.mark.asyncio
    async def test_extract_with_gpu_picks_largest_gpu_model(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(
            gpu={"name": "RTX 4090", "vram_mb": 24576, "count": 1},
            ollama_models=["qwen3.5:0.8b", "qwen3.5:4b", "qwen3.5:27b"],
            ram_available_mb=16384,
        )
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("extract")
        assert result["model"] == "qwen3.5:27b"
        assert result["resource_type"] == "gpu"
        assert result["location"] == "local"

    @pytest.mark.asyncio
    async def test_extract_with_npu_picks_4b_model(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(
            npu_cores=3,
            ollama_models=["qwen3.5:4b", "qwen3.5:0.8b"],
            ram_available_mb=8192,
        )
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("extract")
        assert result["model"] == "qwen3.5:4b"
        assert result["resource_type"] == "npu"

    @pytest.mark.asyncio
    async def test_extract_with_cpu_checks_ram(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(
            ollama_models=["qwen3.5:0.8b", "qwen3.5:2b"],
            ram_available_mb=4096,
        )
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("extract")
        assert result["model"] == "qwen3.5:2b"
        assert result["resource_type"] == "cpu"

    @pytest.mark.asyncio
    async def test_extract_falls_back_to_cluster_worker(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(
            ollama_models=[],
            cluster_workers=[{"name": "gpu-worker", "gpu": True, "models": ["qwen3.5:9b"]}],
        )
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("extract")
        assert result["model"] == "qwen3.5:9b"
        assert result["location"] == "worker:gpu-worker"

    @pytest.mark.asyncio
    async def test_embed_returns_onnx_model(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(npu_cores=3)
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("embed")
        assert result["model"] == "all-MiniLM-L6-v2"
        assert result["resource_type"] == "npu"

    @pytest.mark.asyncio
    async def test_embed_without_npu_uses_cpu(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot()
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("embed")
        assert result["resource_type"] == "cpu"

    @pytest.mark.asyncio
    async def test_unknown_task_type_returns_empty(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(ollama_models=["qwen3.5:4b"])
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("unknown_task")
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_models_returns_empty(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(ollama_models=[], cluster_workers=[])
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        result = await mgr.best_model_for_task("extract")
        assert result == {}


# ---------------------------------------------------------------------------
# evaluate_migration
# ---------------------------------------------------------------------------


class TestEvaluateMigration:
    @pytest.mark.asyncio
    async def test_returns_none_on_first_call(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        snap = _make_snapshot(cluster_workers=[{"name": "w1", "gpu": True}])
        with patch.object(mgr, "get_snapshot", new_callable=AsyncMock, return_value=snap):
            result = await mgr.evaluate_migration()
        assert result is None
        assert mgr._prev_snapshot is snap

    @pytest.mark.asyncio
    async def test_detects_new_gpu_worker_and_upgrades(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        prev_snap = _make_snapshot(cluster_workers=[])
        mgr._prev_snapshot = prev_snap

        new_snap = _make_snapshot(
            cluster_workers=[{"name": "new-gpu", "gpu": True, "models": ["qwen3.5:9b"]}],
        )
        with patch.object(mgr, "get_snapshot", new_callable=AsyncMock, return_value=new_snap):
            result = await mgr.evaluate_migration()
        assert result is not None
        assert result["action"] == "upgrade"
        assert result["to_model"] == "qwen3.5:9b"
        assert "new-gpu" in result["to_location"]

    @pytest.mark.asyncio
    async def test_detects_lost_gpu_worker_and_downgrades(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        prev_snap = _make_snapshot(
            cluster_workers=[{"name": "lost-gpu", "gpu": True, "models": ["qwen3.5:9b"]}],
        )
        mgr._prev_snapshot = prev_snap

        new_snap = _make_snapshot(cluster_workers=[])
        with (
            patch.object(mgr, "get_snapshot", new_callable=AsyncMock, return_value=new_snap),
            patch.object(mgr, "best_model_for_task", new_callable=AsyncMock, return_value={"model": "qwen3.5:4b", "location": "local"}),
        ):
            result = await mgr.evaluate_migration()
        assert result is not None
        assert result["action"] == "downgrade"
        assert "lost-gpu" in result["from_location"]

    @pytest.mark.asyncio
    async def test_detects_busy_gpu_worker_and_downgrades(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager(contention_threshold=30)
        prev_snap = _make_snapshot(
            cluster_workers=[{"name": "busy-gpu", "gpu": True, "gpu_utilisation": 90, "models": ["qwen3.5:9b"]}],
        )
        mgr._prev_snapshot = prev_snap
        mgr._worker_busy_since["busy-gpu"] = time.time() - 60

        new_snap = _make_snapshot(
            cluster_workers=[{"name": "busy-gpu", "gpu": True, "gpu_utilisation": 90, "models": ["qwen3.5:9b"]}],
        )
        with (
            patch.object(mgr, "get_snapshot", new_callable=AsyncMock, return_value=new_snap),
            patch.object(mgr, "best_model_for_task", new_callable=AsyncMock, return_value={"model": "qwen3.5:4b", "location": "local"}),
        ):
            result = await mgr.evaluate_migration()
        assert result is not None
        assert result["action"] == "downgrade"
        assert "busy" in result["reason"].lower() or "utilisation" in result["reason"]

    @pytest.mark.asyncio
    async def test_detects_idle_gpu_worker_and_upgrades_back(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager(idle_upgrade_delay=600)
        prev_snap = _make_snapshot(
            cluster_workers=[{"name": "idle-gpu", "gpu": True, "gpu_utilisation": 10, "models": ["qwen3.5:9b"]}],
        )
        mgr._prev_snapshot = prev_snap
        mgr._worker_idle_since["idle-gpu"] = time.time() - 700

        new_snap = _make_snapshot(
            cluster_workers=[{"name": "idle-gpu", "gpu": True, "gpu_utilisation": 10, "models": ["qwen3.5:9b"]}],
        )
        with patch.object(mgr, "get_snapshot", new_callable=AsyncMock, return_value=new_snap):
            result = await mgr.evaluate_migration()
        assert result is not None
        assert result["action"] == "upgrade"
        assert "idle-gpu" in result["to_location"]

    @pytest.mark.asyncio
    async def test_returns_none_when_no_changes(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        prev_snap = _make_snapshot(
            cluster_workers=[{"name": "stable", "gpu": True, "gpu_utilisation": 50, "models": ["qwen3.5:4b"]}],
        )
        mgr._prev_snapshot = prev_snap

        new_snap = _make_snapshot(
            cluster_workers=[{"name": "stable", "gpu": True, "gpu_utilisation": 50, "models": ["qwen3.5:4b"]}],
        )
        with patch.object(mgr, "get_snapshot", new_callable=AsyncMock, return_value=new_snap):
            result = await mgr.evaluate_migration()
        assert result is None


# ---------------------------------------------------------------------------
# can_accept_job
# ---------------------------------------------------------------------------


class TestCanAcceptJob:
    @pytest.mark.asyncio
    async def test_returns_true_when_no_queue(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        assert await mgr.can_accept_job("cpu") is True

    @pytest.mark.asyncio
    async def test_returns_true_when_capacity_available(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        queue.get_limits.return_value = {"cpu": 4}
        queue.stats.return_value = {"running_by_resource": {"cpu": 2}}
        mgr = ResourceManager(job_queue=queue)

        snap = _make_snapshot()
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        assert await mgr.can_accept_job("cpu") is True

    @pytest.mark.asyncio
    async def test_returns_false_when_at_limit(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        queue.get_limits.return_value = {"cpu": 4}
        queue.stats.return_value = {"running_by_resource": {"cpu": 4}}
        mgr = ResourceManager(job_queue=queue)

        snap = _make_snapshot()
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        assert await mgr.can_accept_job("cpu") is False

    @pytest.mark.asyncio
    async def test_returns_false_when_over_limit(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        queue.get_limits.return_value = {"cpu": 2}
        queue.stats.return_value = {"running_by_resource": {"cpu": 5}}
        mgr = ResourceManager(job_queue=queue)

        snap = _make_snapshot()
        mgr._snapshot = snap
        mgr._last_refresh = time.time()

        assert await mgr.can_accept_job("cpu") is False


# ---------------------------------------------------------------------------
# _best_worker_model
# ---------------------------------------------------------------------------


class TestBestWorkerModel:
    def test_prefers_larger_models(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        worker = {"models": ["qwen3.5:0.8b", "qwen3.5:4b", "qwen3.5:27b"]}
        assert mgr._best_worker_model(worker) == "qwen3.5:27b"

    def test_returns_first_model_if_no_preferred_match(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        worker = {"models": ["custom-model-v1"]}
        assert mgr._best_worker_model(worker) == "custom-model-v1"

    def test_returns_fallback_if_no_models(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        mgr = ResourceManager()
        worker = {"models": []}
        assert mgr._best_worker_model(worker) == "qwen3:4b"


# ---------------------------------------------------------------------------
# Low RAM throttling in _apply_limits
# ---------------------------------------------------------------------------


class TestApplyLimits:
    @pytest.mark.asyncio
    async def test_low_ram_reduces_cpu_and_npu_limits(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        mgr = ResourceManager(job_queue=queue)

        snap = _make_snapshot(
            cpu_cores=8,
            npu_cores=3,
            ram_available_mb=512,
        )
        await mgr._apply_limits(snap)

        queue.set_limit.assert_any_call("cpu", 1)
        queue.set_limit.assert_any_call("npu", 1)

    @pytest.mark.asyncio
    async def test_gpu_count_includes_cluster_workers(self):
        from tinyagentos.scheduling.resource_manager import ResourceManager
        queue = AsyncMock()
        mgr = ResourceManager(job_queue=queue)

        snap = _make_snapshot(
            cpu_cores=4,
            gpu={"name": "RTX 4090", "vram_mb": 24576, "count": 1},
            cluster_workers=[{"name": "w1", "gpu": True}, {"name": "w2", "gpu": True}],
            ram_available_mb=8192,
        )
        await mgr._apply_limits(snap)

        queue.set_limit.assert_any_call("gpu", 3)
