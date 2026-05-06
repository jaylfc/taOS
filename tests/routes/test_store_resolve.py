"""Tests for POST /api/store/resolve — resolver wrapper for the frontend."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.catalog.resolver import DeviceCapability


def make_qwen_manifest():
    m = MagicMock()
    m.id = "qwen2.5-3b"
    m.type = "model"
    m.variants = [
        {
            "id": "q4_k_m",
            "size_mb": 1900,
            "requires": {
                "backends": [
                    {"id": "rk-llama-cpp", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096},
                ],
            },
        },
    ]
    m.context_window = 32768
    return m


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    reg.get_app = MagicMock(return_value=make_qwen_manifest())
    return reg


class TestStoreResolveEndpoint:
    @pytest.mark.asyncio
    async def test_returns_resolve_ok_with_classification(self, client, fake_registry):
        client._transport.app.state.registry = fake_registry
        pi = DeviceCapability(
            device_id="local",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        with patch(
            "tinyagentos.routes.store.get_device_capability",
            new=AsyncMock(return_value=pi),
        ):
            r = await client.post("/api/store/resolve", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "auto",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "ok"
        assert body["backend_id"] == "rk-llama-cpp"
        assert body["action"] in ("use", "install_chain")
        assert body["compat"] in ("green", "amber", "red")

    @pytest.mark.asyncio
    async def test_returns_resolve_err_with_advice(self, client, fake_registry):
        client._transport.app.state.registry = fake_registry
        tiny = DeviceCapability(
            device_id="local",
            targets=("cpu",),
            total_ram_mb=1024,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        with patch(
            "tinyagentos.routes.store.get_device_capability",
            new=AsyncMock(return_value=tiny),
        ):
            r = await client.post("/api/store/resolve", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "err"
        assert "near_miss" in body
        assert "suggestions" in body
        assert body["compat"] == "red"
