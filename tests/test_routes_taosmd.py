"""Tests for /api/taosmd/* routes."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from tinyagentos.routes.taosmd import MEMORY_TIERS


@pytest.mark.asyncio
class TestTiers:
    async def test_get_tiers_returns_three_tiers(self, client):
        resp = await client.get("/api/taosmd/tiers")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"lite", "standard", "heavy"}

    async def test_tier_models_mapping(self, client):
        resp = await client.get("/api/taosmd/tiers")
        data = resp.json()
        assert "nomic-embed-text-v1.5" in data["lite"]["models"]
        assert "bge-m3" in data["standard"]["models"]
        assert "bge-m3" in data["heavy"]["models"]
        assert "qwen3-reranker-0.6b" in data["heavy"]["models"]



def test_memory_tiers_constant_correctness():
    """MEMORY_TIERS constant satisfies spec constraints."""
    assert MEMORY_TIERS["lite"]["needs_accel"] is False
    assert MEMORY_TIERS["standard"]["needs_accel"] is False
    assert MEMORY_TIERS["heavy"]["needs_accel"] is True
    assert MEMORY_TIERS["lite"]["min_ram_mb"] <= 1024
    assert MEMORY_TIERS["standard"]["min_ram_mb"] == 4096
    assert MEMORY_TIERS["heavy"]["min_ram_mb"] == 8192


@pytest.mark.asyncio
class TestDefault:
    async def test_get_default_404_when_not_set(self, client):
        resp = await client.get("/api/taosmd/default")
        assert resp.status_code == 404

    async def test_put_default_round_trip(self, client):
        body = {"device_id": "local", "tier_id": "standard"}
        put_resp = await client.put("/api/taosmd/default", json=body)
        assert put_resp.status_code == 200
        data = put_resp.json()
        assert data["device_id"] == "local"
        assert data["tier_id"] == "standard"
        assert data["tier_name"] == "Standard"

        get_resp = await client.get("/api/taosmd/default")
        assert get_resp.status_code == 200
        assert get_resp.json() == data

    async def test_put_default_unknown_tier_still_saves(self, client):
        """Unknown tier_id should still save — label falls back to the tier_id string."""
        body = {"device_id": "remote-node", "tier_id": "custom-tier"}
        resp = await client.put("/api/taosmd/default", json=body)
        assert resp.status_code == 200
        assert resp.json()["tier_name"] == "custom-tier"


@pytest.mark.asyncio
class TestSetup:
    async def test_post_setup_returns_task_id(self, client):
        with patch(
            "tinyagentos.routes.taosmd._run_setup",
            new=AsyncMock(),
        ):
            resp = await client.post(
                "/api/taosmd/setup",
                json={"device_id": "local", "tier": "lite"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert len(data["task_id"]) == 36  # UUID4

    async def test_post_setup_unknown_tier_returns_400(self, client):
        resp = await client.post(
            "/api/taosmd/setup",
            json={"device_id": "local", "tier": "bogus"},
        )
        assert resp.status_code == 422  # Pydantic rejects the literal

    async def test_get_setup_status_pending(self, client):
        with patch(
            "tinyagentos.routes.taosmd._run_setup",
            new=AsyncMock(),
        ):
            post_resp = await client.post(
                "/api/taosmd/setup",
                json={"device_id": "local", "tier": "standard"},
            )
        task_id = post_resp.json()["task_id"]

        get_resp = await client.get(f"/api/taosmd/setup/{task_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["state"] in {"pending", "downloading", "installing", "done", "failed"}
        assert "progress_pct" in data
        assert "message" in data
        assert "error" in data

    async def test_get_setup_status_404_for_unknown(self, client):
        resp = await client.get("/api/taosmd/setup/nonexistent-task-id")
        assert resp.status_code == 404

    async def test_setup_progresses_to_done_with_mock_installer(self, client, app):
        """Integration: _run_setup drives state to 'done' when the resolver
        picks a backend and the installer succeeds.

        Updated to reflect the resolver-based dispatch from #444 — the old
        code path always tried OllamaInstaller, which 'Ollama is not
        reachable'-failed for users on llama.cpp / rkllama. Now we go
        through resolve() + _BACKEND_TO_METHOD and dispatch the right
        installer based on hardware + installed backends.
        """
        from tinyagentos.routes.taosmd import _run_setup, MEMORY_TIERS
        from types import SimpleNamespace

        tasks: dict = {}
        task_id = "test-task-1"

        # Fake manifest matching what the catalog would yield for
        # nomic-embed-text-v1.5 (Lite tier's only model).
        fake_manifest = SimpleNamespace(
            id="nomic-embed-text-v1.5",
            type="model",
            version="1.5.0",
            variants=[{
                "id": "default",
                "format": "gguf",
                "min_ram_mb": 1024,
                "download_url": "https://example.com/x.gguf",
                "requires": {
                    "backends": [
                        {"id": "ollama",
                         "targets": ["x86-cuda", "cpu"],
                         "min_ram_mb": 1024},
                    ],
                },
            }],
            context_window=8192,
        )
        fake_registry = SimpleNamespace(
            get=lambda _id: fake_manifest if _id == "nomic-embed-text-v1.5" else None,
            mark_installed=lambda *_a, **_kw: None,
        )
        # Hardware profile shaped to dataclasses.asdict — flat dict.
        fake_hw = SimpleNamespace(
            ram_mb=4096,
            cpu={"arch": "x86_64"},
            gpu={"type": "nvidia", "cuda": True, "vram_mb": 12288},
            npu={"type": "none"},
            disk={"free_gb": 100},
            os={"distro": "linux"},
        )

        mock_installer = AsyncMock()
        mock_installer.install = AsyncMock(return_value={"success": True})

        with patch(
            "tinyagentos.installers.base.get_installer",
            return_value=mock_installer,
        ):
            await _run_setup(
                tasks, task_id, "local", "lite", MEMORY_TIERS["lite"],
                registry=fake_registry,
                hardware_profile=fake_hw,
                backends=[{"type": "ollama", "url": "http://localhost:11434", "enabled": True}],
            )

        assert tasks[task_id]["state"] == "done", tasks[task_id]
        assert tasks[task_id]["progress_pct"] == 100

    async def test_setup_marks_failed_on_install_error(self, client, app):
        from tinyagentos.routes.taosmd import _run_setup, MEMORY_TIERS
        from types import SimpleNamespace

        tasks: dict = {}
        task_id = "test-task-2"

        fake_manifest = SimpleNamespace(
            id="nomic-embed-text-v1.5",
            type="model",
            version="1.5.0",
            variants=[{
                "id": "default",
                "format": "gguf",
                "download_url": "https://example.com/x.gguf",
                "requires": {"backends": [{"id": "ollama", "targets": ["cpu"], "min_ram_mb": 1024}]},
            }],
            context_window=0,
        )
        fake_registry = SimpleNamespace(
            get=lambda _id: fake_manifest,
            mark_installed=lambda *_a, **_kw: None,
        )
        fake_hw = SimpleNamespace(
            ram_mb=4096,
            cpu={"arch": "x86_64"},
            gpu={"type": "none"},
            npu={"type": "none"},
            disk={"free_gb": 100},
            os={"distro": "linux"},
        )

        mock_installer = AsyncMock()
        mock_installer.install = AsyncMock(
            return_value={"success": False, "error": "network timeout"},
        )

        with patch(
            "tinyagentos.installers.base.get_installer",
            return_value=mock_installer,
        ):
            await _run_setup(
                tasks, task_id, "local", "lite", MEMORY_TIERS["lite"],
                registry=fake_registry,
                hardware_profile=fake_hw,
                backends=[{"type": "ollama", "enabled": True}],
            )

        assert tasks[task_id]["state"] == "failed"
        assert "network timeout" in tasks[task_id]["error"]


@pytest.mark.asyncio
class TestSetupResolverPath:
    """Resolver-based dispatch — johny's #312 fix. The old code always
    used OllamaInstaller; the new path consults the catalog resolver
    and picks an installer that matches the controller's available
    backends.
    """

    async def test_setup_fails_clearly_when_no_compatible_backend(self):
        from tinyagentos.routes.taosmd import _run_setup, MEMORY_TIERS
        from types import SimpleNamespace

        tasks: dict = {}
        # Manifest only supports an arm-npu backend; controller is x86 cpu-only.
        fake_manifest = SimpleNamespace(
            id="nomic-embed-text-v1.5",
            type="model",
            variants=[{
                "id": "rk",
                "format": "rkllm",
                "download_url": "https://example.com/x.rkllm",
                "requires": {"backends": [{
                    "id": "rkllama", "targets": ["rockchip"], "min_ram_mb": 4096,
                }]},
            }],
            context_window=0,
        )
        fake_registry = SimpleNamespace(get=lambda _id: fake_manifest)
        fake_hw = SimpleNamespace(
            ram_mb=4096,
            cpu={"arch": "x86_64"},
            gpu={"type": "none"},
            npu={"type": "none"},
            disk={"free_gb": 100},
            os={"distro": "linux"},
        )

        await _run_setup(
            tasks, "task-x", "local", "lite", MEMORY_TIERS["lite"],
            registry=fake_registry,
            hardware_profile=fake_hw,
            backends=[],
        )

        assert tasks["task-x"]["state"] == "failed"
        assert "no compatible backend" in tasks["task-x"]["error"].lower()

    async def test_setup_short_circuits_when_registry_missing(self):
        from tinyagentos.routes.taosmd import _run_setup, MEMORY_TIERS

        tasks: dict = {}
        await _run_setup(
            tasks, "task-z", "local", "lite", MEMORY_TIERS["lite"],
            registry=None,
            hardware_profile=None,
            backends=None,
        )

        assert tasks["task-z"]["state"] == "failed"
        assert "registry" in tasks["task-z"]["error"].lower()
