"""Tests for /api/memory/recipes/* routes and the _build_device_info helper."""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.routes.memory_management import _build_device_info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_backend(**overrides):
    """Return an AsyncMock backend with sensible recipe-method defaults."""
    b = MagicMock()
    b.get_recipe_schema = AsyncMock(return_value={"type": "object", "properties": {}})
    b.list_recipes = AsyncMock(return_value=[{"id": "lite", "name": "Lite"}])
    b.get_recipe = AsyncMock(return_value={"id": "lite", "name": "Lite"})
    b.apply_recipe = AsyncMock(return_value={"applied_recipe_id": "lite", "recipe": {"id": "lite"}})
    b.recommend = AsyncMock(return_value=[{"id": "lite", "rationale": "fits lite tier"}])
    b.create_recipe = AsyncMock(side_effect=NotImplementedError("custom recipes are SP3"))
    for k, v in overrides.items():
        setattr(b, k, v)
    return b


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRecipeSchema:
    async def test_get_schema_200(self, client):
        mock_b = _make_mock_backend()
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.get("/api/memory/recipes/schema")
        assert resp.status_code == 200
        assert "properties" in resp.json()

    async def test_get_schema_500_on_backend_error(self, client):
        mock_b = _make_mock_backend()
        mock_b.get_recipe_schema = AsyncMock(side_effect=RuntimeError("db gone"))
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.get("/api/memory/recipes/schema")
        assert resp.status_code == 500
        assert "error" in resp.json()


@pytest.mark.asyncio
class TestListRecipes:
    async def test_list_returns_list(self, client):
        mock_b = _make_mock_backend()
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.get("/api/memory/recipes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["id"] == "lite"

    async def test_list_500_on_backend_error(self, client):
        mock_b = _make_mock_backend()
        mock_b.list_recipes = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.get("/api/memory/recipes")
        assert resp.status_code == 500


@pytest.mark.asyncio
class TestGetRecipe:
    async def test_get_known_recipe(self, client):
        mock_b = _make_mock_backend()
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.get("/api/memory/recipes/lite")
        assert resp.status_code == 200
        assert resp.json()["id"] == "lite"

    async def test_get_unknown_recipe_404(self, client):
        mock_b = _make_mock_backend()
        mock_b.get_recipe = AsyncMock(return_value=None)
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.get("/api/memory/recipes/no-such-recipe")
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()

    async def test_get_recipe_500_on_backend_error(self, client):
        mock_b = _make_mock_backend()
        mock_b.get_recipe = AsyncMock(side_effect=RuntimeError("oops"))
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.get("/api/memory/recipes/lite")
        assert resp.status_code == 500


@pytest.mark.asyncio
class TestApplyRecipe:
    async def test_apply_no_body_uses_global_default(self, client):
        mock_b = _make_mock_backend()
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.post("/api/memory/recipes/lite/apply", content=b"")
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied_recipe_id"] == "lite"
        mock_b.apply_recipe.assert_called_once_with("lite", agent=None)

    async def test_apply_with_agent(self, client):
        mock_b = _make_mock_backend()
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.post(
                "/api/memory/recipes/lite/apply",
                json={"agent": "atlas"},
            )
        assert resp.status_code == 200
        mock_b.apply_recipe.assert_called_once_with("lite", agent="atlas")

    async def test_apply_unknown_recipe_404(self, client):
        mock_b = _make_mock_backend()
        mock_b.apply_recipe = AsyncMock(side_effect=ValueError("unknown recipe id: 'nope'"))
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.post("/api/memory/recipes/nope/apply")
        assert resp.status_code == 404
        assert "unknown recipe" in resp.json()["error"].lower()

    async def test_apply_500_on_backend_error(self, client):
        mock_b = _make_mock_backend()
        mock_b.apply_recipe = AsyncMock(side_effect=RuntimeError("db locked"))
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.post("/api/memory/recipes/lite/apply")
        assert resp.status_code == 500


@pytest.mark.asyncio
class TestRecommend:
    async def test_recommend_no_body_uses_local_device_info(self, client, app):
        """When no device_info provided, _build_device_info is called."""
        mock_b = _make_mock_backend()
        sentinel = {"host": {}, "cluster": {"online_workers": 0, "workers": [], "aggregate": {}}}
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b), \
             patch("tinyagentos.routes.memory_management._build_device_info", return_value=sentinel):
            resp = await client.post("/api/memory/recipes/recommend")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        mock_b.recommend.assert_called_once_with(device_info=sentinel)

    async def test_recommend_explicit_device_info_bypasses_builder(self, client):
        mock_b = _make_mock_backend()
        custom_info = {"host": {"ram_mb": 8192}, "cluster": {}}
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.post(
                "/api/memory/recipes/recommend",
                json={"device_info": custom_info},
            )
        assert resp.status_code == 200
        mock_b.recommend.assert_called_once_with(device_info=custom_info)

    async def test_recommend_500_on_backend_error(self, client):
        mock_b = _make_mock_backend()
        mock_b.recommend = AsyncMock(side_effect=RuntimeError("backend gone"))
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b), \
             patch("tinyagentos.routes.memory_management._build_device_info", return_value={}):
            resp = await client.post("/api/memory/recipes/recommend")
        assert resp.status_code == 500


@pytest.mark.asyncio
class TestCreateRecipe:
    async def test_create_returns_501(self, client):
        """SP3 stub: NotImplementedError → HTTP 501."""
        mock_b = _make_mock_backend()
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.post("/api/memory/recipes", json={"name": "my-recipe"})
        assert resp.status_code == 501
        assert "error" in resp.json()

    async def test_create_invalid_json_400(self, client):
        mock_b = _make_mock_backend()
        with patch("tinyagentos.routes.memory_management._backend", return_value=mock_b):
            resp = await client.post(
                "/api/memory/recipes",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# _build_device_info unit tests
# ---------------------------------------------------------------------------

class TestBuildDeviceInfo:
    """Unit-test the device-info producer without going through HTTP."""

    def _make_request(self, hardware_profile=None, cluster_manager=None):
        state = SimpleNamespace(
            hardware_profile=hardware_profile,
            cluster_manager=cluster_manager,
        )
        app = SimpleNamespace(state=state)
        return SimpleNamespace(app=app)

    def test_no_hardware_no_cluster(self):
        req = self._make_request()
        info = _build_device_info(req)
        assert info["host"] == {}
        assert info["cluster"]["online_workers"] == 0
        assert info["cluster"]["workers"] == []
        agg = info["cluster"]["aggregate"]
        assert agg["max_gpu_vram_mb"] == 0
        assert agg["total_gpu_vram_mb"] == 0
        assert agg["has_npu"] is False
        assert agg["total_cores"] == 0
        assert agg["total_ram_mb"] == 0

    def test_host_hardware_profile_included(self):
        from tinyagentos.hardware import (
            CpuInfo, DiskInfo, GpuInfo, HardwareProfile, NpuInfo, OsInfo,
        )
        hp = HardwareProfile(
            ram_mb=16384,
            cpu=CpuInfo(arch="x86_64", cores=8),
            gpu=GpuInfo(type="nvidia", vram_mb=12288, cuda=True),
            npu=NpuInfo(type="none"),
            disk=DiskInfo(total_gb=512, free_gb=200),
            os=OsInfo(distro="ubuntu"),
        )
        req = self._make_request(hardware_profile=hp)
        info = _build_device_info(req)
        assert info["host"]["ram_mb"] == 16384
        assert info["host"]["profile_id"] == hp.profile_id
        assert info["cluster"]["online_workers"] == 0

    def _make_worker(self, name, status, hardware):
        return SimpleNamespace(
            name=name,
            status=status,
            hardware=hardware,
            capabilities=["embed"],
            tier_id="x86-cuda-12gb",
        )

    def test_aggregate_single_worker(self):
        w = self._make_worker("gpu1", "online", {
            "ram_mb": 32768,
            "cpu": {"cores": 16},
            "gpu": {"vram_mb": 24576, "type": "nvidia"},
            "npu": {"type": "none"},
        })
        cluster_manager = SimpleNamespace(get_workers=lambda: [w])
        req = self._make_request(cluster_manager=cluster_manager)
        info = _build_device_info(req)
        agg = info["cluster"]["aggregate"]
        assert agg["max_gpu_vram_mb"] == 24576
        assert agg["total_gpu_vram_mb"] == 24576
        assert agg["has_npu"] is False
        assert agg["total_cores"] == 16
        assert agg["total_ram_mb"] == 32768
        assert info["cluster"]["online_workers"] == 1

    def test_aggregate_two_workers(self):
        w1 = self._make_worker("gpu1", "online", {
            "ram_mb": 32768,
            "cpu": {"cores": 16},
            "gpu": {"vram_mb": 24576},
            "npu": {"type": "none"},
        })
        w2 = self._make_worker("npu1", "online", {
            "ram_mb": 16384,
            "cpu": {"cores": 8},
            "gpu": {"vram_mb": 0},
            "npu": {"type": "rknpu"},
        })
        cluster_manager = SimpleNamespace(get_workers=lambda: [w1, w2])
        req = self._make_request(cluster_manager=cluster_manager)
        info = _build_device_info(req)
        agg = info["cluster"]["aggregate"]
        assert agg["max_gpu_vram_mb"] == 24576
        assert agg["total_gpu_vram_mb"] == 24576
        assert agg["has_npu"] is True
        assert agg["total_cores"] == 24
        assert agg["total_ram_mb"] == 49152
        assert info["cluster"]["online_workers"] == 2

    def test_offline_workers_excluded(self):
        online = self._make_worker("gpu1", "online", {
            "ram_mb": 8192, "cpu": {"cores": 4},
            "gpu": {"vram_mb": 8192}, "npu": {"type": "none"},
        })
        offline = self._make_worker("gpu2", "offline", {
            "ram_mb": 65536, "cpu": {"cores": 32},
            "gpu": {"vram_mb": 80000}, "npu": {"type": "none"},
        })
        cluster_manager = SimpleNamespace(get_workers=lambda: [online, offline])
        req = self._make_request(cluster_manager=cluster_manager)
        info = _build_device_info(req)
        assert info["cluster"]["online_workers"] == 1
        agg = info["cluster"]["aggregate"]
        assert agg["total_ram_mb"] == 8192
        assert agg["max_gpu_vram_mb"] == 8192

    def test_aggregate_missing_fields_handled(self):
        """Workers with empty/missing hardware dicts don't crash."""
        w = self._make_worker("bare", "online", {})
        cluster_manager = SimpleNamespace(get_workers=lambda: [w])
        req = self._make_request(cluster_manager=cluster_manager)
        info = _build_device_info(req)
        agg = info["cluster"]["aggregate"]
        assert agg["total_ram_mb"] == 0
        assert agg["total_cores"] == 0
        assert agg["has_npu"] is False
