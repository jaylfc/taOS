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


class TestBackendToMethodMapping:
    """Regression tests for _BACKEND_TO_METHOD and the real get_installer lookup.

    These tests do NOT mock get_installer — they let the real lookup run so
    that a future regression (e.g. adding an entry pointing to an unknown
    method) would surface as a ValueError rather than an HTTP 500 in prod.
    The underlying installers' .install() methods are mocked to avoid I/O.
    """

    def test_ollama_maps_to_download_no_value_error(self):
        """get_installer(_BACKEND_TO_METHOD['ollama']) must not raise."""
        from tinyagentos.routes.store_install import _BACKEND_TO_METHOD
        from tinyagentos.installers.base import get_installer

        method = _BACKEND_TO_METHOD["ollama"]
        assert method == "download"
        # Should not raise ValueError
        installer = get_installer(method)
        assert installer is not None

    def test_rk_llama_cpp_maps_to_download_no_value_error(self):
        """get_installer(_BACKEND_TO_METHOD['rk-llama-cpp']) must not raise."""
        from tinyagentos.routes.store_install import _BACKEND_TO_METHOD
        from tinyagentos.installers.base import get_installer

        method = _BACKEND_TO_METHOD["rk-llama-cpp"]
        assert method == "download"
        # Should not raise ValueError
        installer = get_installer(method)
        assert installer is not None

    def test_all_entries_resolve_to_valid_installer_methods(self):
        """Every entry in _BACKEND_TO_METHOD must be a valid get_installer key."""
        from tinyagentos.routes.store_install import _BACKEND_TO_METHOD
        from tinyagentos.installers.base import get_installer

        for backend_id, method in _BACKEND_TO_METHOD.items():
            try:
                installer = get_installer(method)
                assert installer is not None, f"{backend_id!r} → {method!r} returned None"
            except ValueError as exc:
                raise AssertionError(
                    f"_BACKEND_TO_METHOD[{backend_id!r}] = {method!r} is not a valid "
                    f"get_installer key: {exc}"
                ) from exc

    @pytest.mark.asyncio
    async def test_unknown_backend_returns_500_not_exception(self, client, fake_registry):
        """A backend_id absent from _BACKEND_TO_METHOD returns HTTP 500, not a traceback."""
        # Construct a manifest whose variant declares an unmapped backend.
        unknown_manifest = MagicMock()
        unknown_manifest.id = "test-model"
        unknown_manifest.type = "model"
        unknown_manifest.variants = [
            {
                "id": "v1",
                "size_mb": 100,
                "download_url": "https://example/model.bin",
                "requires": {
                    "backends": [
                        {"id": "unknown-backend-xyz", "targets": ["cpu"], "min_ram_mb": 512},
                    ],
                },
            }
        ]
        unknown_manifest.context_window = 4096
        unknown_manifest.hardware_tiers = {}
        unknown_manifest.install = {}
        unknown_manifest.version = "1.0.0"

        fat_cap = DeviceCapability(
            device_id="local",
            targets=("cpu",),
            total_ram_mb=32768,
            total_vram_mb=0,
            free_disk_mb=100_000,
            installed_backends=("unknown-backend-xyz",),
        )

        reg = MagicMock()
        reg.get_app = MagicMock(return_value=unknown_manifest)
        reg.get = MagicMock(return_value=unknown_manifest)
        reg.mark_installed = MagicMock()
        reg.list_available = MagicMock(return_value=[])
        client._transport.app.state.registry = reg

        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=fat_cap),
        ):
            r = await client.post("/api/store/install-v2", json={
                "manifest_id": "test-model",
                "variant_id": "v1",
            })
        assert r.status_code == 500
        assert "_BACKEND_TO_METHOD" in r.json()["error"]


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
