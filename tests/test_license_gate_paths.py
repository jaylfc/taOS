"""Every install path goes through the same licence gate.

One test per path: v1 route (used by `taosctl store install`), v2 route is in
tests/routes/test_store_install_v2.py, model download, taOSmd setup, the shared
_legacy_install (setup wizard backends, v2 services), and a backend pulled in by
the v2 install chain. An agent or CLI token gets the 412 with the text to show
the person; it can never accept for them.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from taos_test_csrf import csrf_event_hooks


def _manifest(mid="gated-app", mtype="service", **fields):
    m = MagicMock()
    m.id = mid
    m.name = "Gated App"
    m.type = mtype
    m.license = ""
    m.weights_license = "CC-BY-NC 4.0"
    m.model_license = ""
    m.license_class = ""
    m.license_version = ""
    m.license_url = ""
    m.license_excluded_territories = []
    m.install = {"method": "docker", "ports": [8080]}
    m.variants = [{"id": "v1", "download_url": "https://example.invalid/m.gguf", "size_mb": 10}]
    m.requires = {}
    m.hardware_tiers = {}
    m.version = "1.0.0"
    for k, v in fields.items():
        setattr(m, k, v)
    return m


def _registry(manifest):
    reg = MagicMock()
    reg.get = MagicMock(return_value=manifest)
    reg.get_app = MagicMock(return_value=manifest)
    reg.is_installed = MagicMock(return_value=False)
    reg.mark_installed = MagicMock()
    reg.list_available = MagicMock(return_value=[])
    return reg


def _token_client(app):
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {app.state.auth.get_local_token()}"},
        event_hooks=csrf_event_hooks(),
    )


def _installer():
    inst = MagicMock()
    inst.install = AsyncMock(return_value={"success": True})
    return inst


class TestV1StoreInstall:
    """/api/store/install is what `taosctl store install` calls."""

    @pytest.mark.asyncio
    async def test_session_without_acceptance_gets_412_and_nothing_installs(self, client):
        app = client._transport.app
        app.state.registry = _registry(_manifest())
        inst = _installer()
        with patch("tinyagentos.routes.store.get_installer", return_value=inst):
            r = await client.post("/api/store/install", json={"app_id": "gated-app"})
        assert r.status_code == 412
        assert r.json()["needs_license_acceptance"] is True and r.json()["text"]
        inst.install.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_token_client_gets_412_with_text_and_cannot_accept(self, client):
        app = client._transport.app
        app.state.registry = _registry(_manifest())
        inst = _installer()
        async with _token_client(app) as cli:
            with patch("tinyagentos.routes.store.get_installer", return_value=inst):
                r1 = await cli.post("/api/store/install", json={"app_id": "gated-app"})
                r2 = await cli.post("/api/store/install", json={"app_id": "gated-app", "accepted": True})
        assert r1.status_code == 412 and "CC-BY-NC 4.0" in r1.json()["text"]
        assert r2.status_code == 403 and r2.json()["error"] == "agent_cannot_accept_license"
        inst.install.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_person_acceptance_then_installs(self, client):
        app = client._transport.app
        app.state.registry = _registry(_manifest())
        inst = _installer()
        with patch("tinyagentos.routes.store.get_installer", return_value=inst):
            r = await client.post("/api/store/install", json={"app_id": "gated-app", "accepted": True})
        assert r.status_code == 200
        inst.install.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_permissive_manifest_unaffected(self, client):
        app = client._transport.app
        app.state.registry = _registry(_manifest(weights_license="", license="MIT"))
        inst = _installer()
        with patch("tinyagentos.routes.store.get_installer", return_value=inst):
            r = await client.post("/api/store/install", json={"app_id": "gated-app"})
        assert r.status_code == 200


class TestModelDownload:
    @pytest.mark.asyncio
    async def test_download_is_gated(self, client):
        app = client._transport.app
        app.state.registry = _registry(_manifest(mid="gated-model", mtype="model"))
        dm = MagicMock()
        dm.start = AsyncMock()
        app.state.download_manager = dm
        r = await client.post("/api/models/download", json={"app_id": "gated-model", "variant_id": "v1"})
        assert r.status_code == 412 and r.json()["needs_license_acceptance"] is True
        assert not dm.method_calls

    @pytest.mark.asyncio
    async def test_token_cannot_accept_for_download(self, client):
        app = client._transport.app
        app.state.registry = _registry(_manifest(mid="gated-model", mtype="model"))
        async with _token_client(app) as cli:
            r = await cli.post("/api/models/download",
                               json={"app_id": "gated-model", "variant_id": "v1", "accepted": True})
        assert r.status_code == 403 and r.json()["error"] == "agent_cannot_accept_license"


class TestTaosmdSetup:
    @pytest.mark.asyncio
    async def test_setup_with_gated_model_is_refused_before_anything_starts(self, client):
        app = client._transport.app
        app.state.registry = _registry(_manifest(mid="nomic-embed-text-v1.5", mtype="model"))
        with patch("tinyagentos.routes.taosmd.asyncio.create_task") as ct:
            r = await client.post("/api/taosmd/setup", json={"device_id": "local", "tier": "lite"})
        assert r.status_code == 412 and r.json()["needs_license_acceptance"] is True
        ct.assert_not_called()


class TestSharedLegacyInstall:
    """_legacy_install backs the setup wizard's backend installs and v2 services."""

    @staticmethod
    def _request(app, via="local_token"):
        req = Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "app": app})
        req.state.via = via
        return req

    @pytest.mark.asyncio
    async def test_legacy_install_is_gated(self, client):
        from tinyagentos.routes.store_install import _legacy_install
        app = client._transport.app
        app.state.registry = _registry(_manifest())
        inst = _installer()
        with patch("tinyagentos.routes.store_install.get_installer", return_value=inst):
            resp = await _legacy_install(self._request(app), {}, "gated-app", None)
        assert resp.status_code == 412
        inst.install.assert_not_awaited()


class TestV2ChainBackend:
    @pytest.mark.asyncio
    async def test_gated_backend_in_chain_blocks_before_install(self, client):
        from tinyagentos.catalog.resolver import DeviceCapability
        app = client._transport.app
        model = MagicMock()
        model.id = "qwen-x"; model.name = "Qwen X"; model.type = "model"
        model.license = "MIT"; model.weights_license = ""; model.model_license = ""
        model.license_class = ""; model.license_version = ""; model.license_url = ""
        model.license_excluded_territories = []
        model.variants = [{"id": "q4", "size_mb": 1900, "download_url": "https://example.invalid/q4.gguf",
                           "requires": {"backends": [{"id": "rk-llama-cpp", "targets": ["rockchip"], "min_ram_mb": 4096}]}}]
        model.context_window = 32768; model.hardware_tiers = {}; model.install = {}; model.version = "1"
        backend = _manifest(mid="rk-llama-cpp", install={"method": "script", "script": "x.sh"})
        reg = MagicMock()
        reg.get_app = MagicMock(side_effect=lambda i: {"qwen-x": model, "rk-llama-cpp": backend}.get(i))
        reg.get = reg.get_app
        reg.mark_installed = MagicMock(); reg.list_available = MagicMock(return_value=[])
        app.state.registry = reg
        cap = DeviceCapability(device_id="local", targets=("rockchip", "cpu"), total_ram_mb=16384,
                               total_vram_mb=0, free_disk_mb=50000, installed_backends=())
        with patch("tinyagentos.routes.store_install.get_device_capability", new=AsyncMock(return_value=cap)), \
             patch("tinyagentos.routes.store_install.get_installer") as gi:
            r = await client.post("/api/store/install-v2", json={"manifest_id": "qwen-x", "variant_id": "q4"})
        assert r.status_code == 412 and r.json()["needs_license_acceptance"] is True
        gi.assert_not_called()


class TestCliShowsWhatToAsk:
    def test_412_error_text_carries_the_licence_text(self):
        from tinyagentos.cli.taosctl.client import _extract_error
        import json
        raw = json.dumps({"needs_license_acceptance": True, "text": "App uses X. Only you can accept."}).encode()
        assert "Only you can accept" in _extract_error(raw, 412)
        raw = json.dumps({"needs_country": True, "detail": "Set your country in Settings."}).encode()
        assert "Set your country" in _extract_error(raw, 412)


class TestMcpMarketplaceInstall:
    @pytest.mark.asyncio
    async def test_restrictive_mcp_server_is_gated(self, client):
        app = client._transport.app
        mp = MagicMock()
        mp.detail = AsyncMock(return_value={"name": "Odd Server", "license": "CC-BY-NC-4.0"})
        mp.install = AsyncMock(return_value={"ok": True})
        app.state.mcp_marketplace = mp
        r = await client.post("/api/mcp/marketplace/servers/odd/install")
        assert r.status_code == 412 and r.json()["needs_license_acceptance"] is True
        mp.install.assert_not_awaited()
