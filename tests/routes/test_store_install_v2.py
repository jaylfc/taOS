"""Integration tests for the resolver-driven /api/store/install-v2 dispatcher.

Mocks AppRegistry + ClusterManager so we can drive the dispatcher through
its three branches (use, install_chain, force-archive) without needing a
real worker. Real installer side effects are mocked at the
`get_installer` boundary.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.catalog.resolver import DeviceCapability


def make_qwen_manifest():
    """In-memory manifest matching the post-migration shape."""
    m = MagicMock()
    m.id = "qwen2.5-3b"
    m.type = "model"
    m.variants = [
        {
            "id": "q4_k_m",
            "size_mb": 1900,
            "download_url": "https://example/q4.gguf",
            "requires": {
                "backends": [
                    {"id": "rk-llama-cpp", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096},
                ],
            },
        },
    ]
    m.context_window = 32768
    m.hardware_tiers = {}
    m.install = {}
    m.version = "2.5.0"
    return m


def make_backend_service():
    m = MagicMock()
    m.id = "rk-llama-cpp"
    m.type = "service"
    m.install = {"method": "script", "script": "scripts/install-rkllamacpp.sh"}
    m.requires = {}
    m.hardware_tiers = {}
    m.version = "0.1.0"
    return m


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    qwen = make_qwen_manifest()
    backend = make_backend_service()

    def _get_app(app_id):
        return {"qwen2.5-3b": qwen, "rk-llama-cpp": backend}.get(app_id)

    reg.get_app = MagicMock(side_effect=_get_app)
    reg.get = MagicMock(side_effect=_get_app)
    reg.mark_installed = MagicMock()
    reg.list_available = MagicMock(return_value=[])
    return reg


@pytest.fixture
def pi_capability():
    return DeviceCapability(
        device_id="local",
        targets=("rockchip-rk3588", "cpu"),
        total_ram_mb=16384,
        total_vram_mb=0,
        free_disk_mb=50_000,
        installed_backends=(),
    )


class TestInstallChainHappyPath:
    @pytest.mark.asyncio
    async def test_chains_backend_then_model(self, client, fake_registry, pi_capability):
        client._transport.app.state.registry = fake_registry
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=pi_capability),
        ), patch(
            "tinyagentos.routes.store_install.get_installer"
        ) as mock_get:
            backend_inst = MagicMock()
            backend_inst.install = AsyncMock(return_value={"success": True, "method": "script"})
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True, "runtime_location": {"host": "localhost", "port": 8090}})
            mock_get.side_effect = [backend_inst, model_inst]
            r = await client.post("/api/store/install-v2", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["chain"][0]["step"] == "backend"
        assert body["chain"][0]["status"] == "installed"
        assert body["chain"][1]["step"] == "model"
        assert body["chain"][1]["status"] == "installed"


class TestInstallChainBackendFailure:
    @pytest.mark.asyncio
    async def test_returns_500_when_backend_install_fails(self, client, fake_registry, pi_capability):
        client._transport.app.state.registry = fake_registry
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=pi_capability),
        ), patch(
            "tinyagentos.routes.store_install.get_installer"
        ) as mock_get:
            backend_inst = MagicMock()
            backend_inst.install = AsyncMock(return_value={"success": False, "error": "build failed"})
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True})
            mock_get.side_effect = [backend_inst, model_inst]
            r = await client.post("/api/store/install-v2", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 500
        assert "backend" in r.json()["error"].lower()


class TestResolveErrorReturns422:
    @pytest.mark.asyncio
    async def test_returns_structured_error_with_suggestions(self, client, fake_registry):
        tiny = DeviceCapability(
            device_id="tiny",
            targets=("cpu",),
            total_ram_mb=1024,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        client._transport.app.state.registry = fake_registry
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=tiny),
        ):
            r = await client.post("/api/store/install-v2", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 422
        body = r.json()
        assert "near_miss" in body
        assert "suggestions" in body
        assert isinstance(body["suggestions"], list)
