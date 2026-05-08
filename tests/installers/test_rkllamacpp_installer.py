"""Tests for RkLlamaCppInstaller.

Focus: env-var resolution + the failure-path semantics fixed in response
to CodeRabbit on the original PR #322 (success: False when systemctl
fails or /health doesn't return 200).
"""
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest



class TestEnvVarOverrides:
    def test_default_install_dir_no_env(self, monkeypatch, tmp_path):
        from tinyagentos.installers.rkllamacpp_installer import _default_install_dir
        monkeypatch.delenv("TAOS_RKLLAMACPP_DIR", raising=False)
        # Default uses Path.home() / "rk-llama.cpp"
        assert _default_install_dir() == Path.home() / "rk-llama.cpp"

    def test_install_dir_from_env(self, monkeypatch, tmp_path):
        from tinyagentos.installers.rkllamacpp_installer import _default_install_dir
        monkeypatch.setenv("TAOS_RKLLAMACPP_DIR", str(tmp_path / "custom"))
        assert _default_install_dir() == tmp_path / "custom"

    def test_default_port_no_env(self, monkeypatch):
        from tinyagentos.installers.rkllamacpp_installer import _default_port
        monkeypatch.delenv("TAOS_RKLLAMACPP_PORT", raising=False)
        assert _default_port() == 8090

    def test_port_from_env(self, monkeypatch):
        from tinyagentos.installers.rkllamacpp_installer import _default_port
        monkeypatch.setenv("TAOS_RKLLAMACPP_PORT", "9999")
        assert _default_port() == 9999

    def test_port_from_env_invalid_falls_back(self, monkeypatch):
        from tinyagentos.installers.rkllamacpp_installer import _default_port
        monkeypatch.setenv("TAOS_RKLLAMACPP_PORT", "not-a-number")
        assert _default_port() == 8090

    def test_explicit_args_override_env(self, monkeypatch, tmp_path):
        from tinyagentos.installers.rkllamacpp_installer import RkLlamaCppInstaller
        monkeypatch.setenv("TAOS_RKLLAMACPP_DIR", "/should-not-be-used")
        monkeypatch.setenv("TAOS_RKLLAMACPP_PORT", "9999")
        i = RkLlamaCppInstaller(install_dir=tmp_path / "explicit", port=7777)
        assert i.install_dir == tmp_path / "explicit"
        assert i.port == 7777


@pytest.fixture
def sandboxed_models_root(tmp_path, monkeypatch):
    """Point TAOS_MODELS_ROOT at tmp_path so the installer creates the
    new shared layout (~/models/<backend>/<family>/<id>/) inside the
    test sandbox instead of the real home directory.
    """
    root = tmp_path / "models"
    monkeypatch.setenv("TAOS_MODELS_ROOT", str(root))
    return root


class TestInstallReturnsFailureOnSystemctlError:
    """If systemctl restart fails, the model file is on disk but the runtime
    is not serving — we must NOT report success. (CR finding from PR #322.)"""

    @pytest.mark.asyncio
    async def test_systemctl_failure_returns_success_false(self, tmp_path, sandboxed_models_root):
        from tinyagentos.installers.rkllamacpp_installer import RkLlamaCppInstaller

        # Pre-create the binary so the precondition check passes.
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "llama-server").write_text("fake")

        installer = RkLlamaCppInstaller(install_dir=tmp_path, port=8090)

        with patch.object(installer, "_download", new=AsyncMock()), \
             patch.object(installer, "_systemctl", new=AsyncMock(side_effect=RuntimeError("unit not loaded"))):
            result = await installer.install(
                "fake-app",
                install_config={"method": "rkllamacpp"},
                variant={"id": "q4", "size_mb": 100, "download_url": "https://example/x.gguf"},
            )

        assert result["success"] is False
        assert "systemctl failed" in result["error"]


class TestInstallReturnsFailureOnHealthCheckTimeout:
    """If /health doesn't return 200 within the timeout, the model isn't
    actually usable — we must NOT report success. (CR finding from PR #322.)"""

    @pytest.mark.asyncio
    async def test_health_timeout_returns_success_false(self, tmp_path, sandboxed_models_root):
        from tinyagentos.installers.rkllamacpp_installer import RkLlamaCppInstaller

        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "llama-server").write_text("fake")

        installer = RkLlamaCppInstaller(install_dir=tmp_path, port=8090)

        with patch.object(installer, "_download", new=AsyncMock()), \
             patch.object(installer, "_systemctl", new=AsyncMock()), \
             patch.object(installer, "_wait_for_server", new=AsyncMock(return_value=False)):
            result = await installer.install(
                "fake-app",
                install_config={"method": "rkllamacpp"},
                variant={"id": "q4", "size_mb": 100, "download_url": "https://example/x.gguf"},
            )

        assert result["success"] is False
        assert "health" in result["error"].lower() or "200" in result["error"]


class TestInstallSucceedsHappyPath:
    @pytest.mark.asyncio
    async def test_full_install_returns_success(self, tmp_path, sandboxed_models_root):
        from tinyagentos.installers.rkllamacpp_installer import RkLlamaCppInstaller

        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "llama-server").write_text("fake")

        installer = RkLlamaCppInstaller(install_dir=tmp_path, port=8090)

        with patch.object(installer, "_download", new=AsyncMock()), \
             patch.object(installer, "_systemctl", new=AsyncMock()), \
             patch.object(installer, "_wait_for_server", new=AsyncMock(return_value=True)):
            result = await installer.install(
                "fake-app",
                install_config={"method": "rkllamacpp"},
                variant={"id": "q4", "size_mb": 100, "download_url": "https://example/x.gguf"},
            )

        assert result["success"] is True
        assert result["service_running"] is True
        assert result["endpoint"] == "http://localhost:8090"


class TestInstallUsesSharedLayout:
    """The installer writes to ~/models/<backend>/<family>/<manifest_id>/
    (per the layout decision in #430), not the legacy install_dir/models/."""

    @pytest.mark.asyncio
    async def test_target_path_under_shared_root(self, tmp_path, sandboxed_models_root):
        from tinyagentos.installers.rkllamacpp_installer import (
            BACKEND_ID,
            RkLlamaCppInstaller,
        )

        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "llama-server").write_text("fake")

        installer = RkLlamaCppInstaller(install_dir=tmp_path, port=8090)
        captured: dict = {}

        async def fake_download(url, dest, expected_sha256):
            captured["dest"] = dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("fake gguf bytes")

        with patch.object(installer, "_download", side_effect=fake_download), \
             patch.object(installer, "_systemctl", new=AsyncMock()), \
             patch.object(installer, "_wait_for_server", new=AsyncMock(return_value=True)):
            result = await installer.install(
                "gemma-4-e2b-gguf",
                install_config={"method": "rkllamacpp"},
                variant={
                    "id": "q4_k_m",
                    "size_mb": 100,
                    "download_url": "https://hf.co/x/y/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf",
                },
            )

        assert result["success"] is True
        # Lands at <root>/<backend>/<family>/<id>/<original-filename>
        expected = (
            sandboxed_models_root
            / BACKEND_ID
            / "gemma"
            / "gemma-4-e2b-gguf"
            / "gemma-4-E2B-it-Q4_K_M.gguf"
        )
        assert captured["dest"] == expected
        # active.gguf is in install_dir, points at the file in the new tree
        active = tmp_path / "active.gguf"
        assert active.is_symlink()
        assert active.resolve() == expected.resolve()
