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


class TestInstallReturnsFailureOnSystemctlError:
    """If systemctl restart fails, the model file is on disk but the runtime
    is not serving — we must NOT report success. (CR finding from PR #322.)"""

    @pytest.mark.asyncio
    async def test_systemctl_failure_returns_success_false(self, tmp_path):
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
    async def test_health_timeout_returns_success_false(self, tmp_path):
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
    async def test_full_install_returns_success(self, tmp_path):
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
