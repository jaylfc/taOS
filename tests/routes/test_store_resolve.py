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
                    {"id": "rk-llama-cpp", "targets": ["rockchip"], "min_ram_mb": 4096},
                ],
            },
        },
    ]
    m.context_window = 32768
    return m


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    reg.get = MagicMock(return_value=make_qwen_manifest())
    return reg


class TestStoreResolveEndpoint:
    @pytest.mark.asyncio
    async def test_returns_resolve_ok_with_classification(self, client, fake_registry):
        client._transport.app.state.registry = fake_registry
        pi = DeviceCapability(
            device_id="local",
            targets=("rockchip", "cpu"),
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


class TestStoreResolveBatch:
    @pytest.mark.asyncio
    async def test_batch_of_two_known_ids_returns_same_as_single_route(
        self, client, fake_registry
    ):
        client._transport.app.state.registry = fake_registry
        pi = DeviceCapability(
            device_id="local",
            targets=("rockchip", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        with patch(
            "tinyagentos.routes.store.get_device_capability",
            new=AsyncMock(return_value=pi),
        ) as mock_gdc:
            r1 = await client.post("/api/store/resolve", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "auto",
            })
            r2 = await client.post("/api/store/resolve-batch", json={
                "manifest_ids": ["qwen2.5-3b", "qwen2.5-3b"],
                "variant_id": "auto",
            })
        assert r1.status_code == 200
        assert r2.status_code == 200
        body1 = r1.json()
        body2 = r2.json()
        # Each id in the batch result should match the single-route result
        assert body2["results"]["qwen2.5-3b"] == body1

    @pytest.mark.asyncio
    async def test_batch_device_capability_awaited_once_for_3_id_batch(
        self, client, fake_registry
    ):
        client._transport.app.state.registry = fake_registry
        pi = DeviceCapability(
            device_id="local",
            targets=("rockchip", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        with patch(
            "tinyagentos.routes.store.get_device_capability",
            new=AsyncMock(return_value=pi),
        ) as mock_gdc:
            r = await client.post("/api/store/resolve-batch", json={
                "manifest_ids": ["qwen2.5-3b", "qwen2.5-3b", "qwen2.5-3b"],
                "variant_id": "auto",
            })
        assert r.status_code == 200
        # get_device_capability must be awaited exactly once for the whole batch
        mock_gdc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_batch_unknown_id_yields_error_known_id_still_resolves(
        self, client
    ):
        reg = MagicMock()
        reg.get = MagicMock(
            side_effect=lambda i: make_qwen_manifest() if i == "qwen2.5-3b" else None
        )
        client._transport.app.state.registry = reg
        pi = DeviceCapability(
            device_id="local",
            targets=("rockchip", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        with patch(
            "tinyagentos.routes.store.get_device_capability",
            new=AsyncMock(return_value=pi),
        ):
            r = await client.post("/api/store/resolve-batch", json={
                "manifest_ids": ["qwen2.5-3b", "unknown-model"],
                "variant_id": "auto",
            })
        assert r.status_code == 200
        body = r.json()
        # Known id resolves, unknown id yields error entry
        assert body["results"]["qwen2.5-3b"]["result"] == "ok"
        assert "error" in body["results"]["unknown-model"]

    @pytest.mark.asyncio
    async def test_batch_too_many_ids_400(self, client):
        client._transport.app.state.registry = MagicMock()
        r = await client.post("/api/store/resolve-batch", json={
            "manifest_ids": ["id"] * 201,
            "variant_id": "auto",
        })
        assert r.status_code == 400
        assert r.json()["error"] == "at most 200 manifest_ids per request"

    @pytest.mark.asyncio
    async def test_batch_non_list_manifest_ids_400(self, client):
        client._transport.app.state.registry = MagicMock()
        r = await client.post("/api/store/resolve-batch", json={
            "manifest_ids": "not-a-list",
            "variant_id": "auto",
        })
        assert r.status_code == 400
        assert r.json()["error"] == "manifest_ids must be a list of strings"
