import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from tinyagentos.installers.base import get_installer
from tinyagentos.installers.pip_installer import PipInstaller
from tinyagentos.installers.docker_installer import DockerInstaller
from tinyagentos.installers.download_installer import DownloadInstaller


class TestGetInstaller:
    def test_returns_pip(self):
        assert isinstance(get_installer("pip"), PipInstaller)

    def test_returns_docker(self):
        assert isinstance(get_installer("docker"), DockerInstaller)

    def test_returns_download(self):
        assert isinstance(get_installer("download"), DownloadInstaller)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown install method"):
            get_installer("unknown")


class TestPipInstaller:
    @pytest.mark.asyncio
    async def test_install_creates_venv(self, tmp_path):
        installer = PipInstaller(apps_dir=tmp_path)
        with patch("tinyagentos.installers.pip_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.install("testapp", {"method": "pip", "package": "testpkg"})
            assert result["success"] is True
            # Should have called python -m venv and pip install
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("venv" in c for c in calls)
            assert any("pip" in c and "testpkg" in c for c in calls)

    @pytest.mark.asyncio
    async def test_uninstall_removes_dir(self, tmp_path):
        installer = PipInstaller(apps_dir=tmp_path)
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "venv").mkdir()
        result = await installer.uninstall("testapp")
        assert result["success"] is True
        assert not app_dir.exists()


class TestDockerInstaller:
    @pytest.mark.asyncio
    async def test_install_writes_compose(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        install_config = {
            "method": "docker",
            "image": "gitea/gitea:1.22",
            "volumes": ["data:/data"],
            "env": {"ROOT_URL": "http://localhost:3000"},
        }
        with patch("tinyagentos.installers.docker_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.install("gitea", install_config)
            assert result["success"] is True
            compose_file = tmp_path / "gitea" / "docker-compose.yaml"
            assert compose_file.exists()

    def test_generate_compose_declares_named_volumes_and_omits_version(self, tmp_path):
        # Regression: named volumes (e.g. searxng's "config:/etc/searxng") must
        # be declared at the top level or compose rejects the project with
        # "refers to undefined volume". The obsolete `version` key is dropped.
        installer = DockerInstaller(apps_dir=tmp_path)
        compose = installer._generate_compose("searxng", {
            "image": "searxng/searxng:latest",
            "volumes": ["config:/etc/searxng", "/host/path:/data"],
            "ports": [8080],
        })
        assert "version" not in compose
        assert compose["volumes"] == {"config": None}  # only the named volume
        assert compose["services"]["searxng"]["volumes"] == ["config:/etc/searxng", "/host/path:/data"]
        assert compose["services"]["searxng"]["ports"] == ["8080:8080"]

    def test_generate_compose_omits_volumes_block_for_bind_mounts_only(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        compose = installer._generate_compose("app", {
            "image": "x:1",
            "volumes": ["/host:/data", "./rel:/r", "~/h:/hh"],
        })
        assert "volumes" not in compose  # no named volumes → no top-level block

    @pytest.mark.asyncio
    async def test_start_runs_compose_up(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        app_dir = tmp_path / "gitea"
        app_dir.mkdir()
        (app_dir / "docker-compose.yaml").write_text("version: '3'")
        with patch("tinyagentos.installers.docker_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.start("gitea")
            assert result["success"] is True
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("up" in c and "-d" in c for c in calls)


class TestDownloadInstaller:
    @pytest.mark.asyncio
    async def test_install_downloads_file(self, tmp_path):
        installer = DownloadInstaller(models_dir=tmp_path)
        variant = {
            "id": "q4_k_m",
            "download_url": "https://example.com/model.gguf",
            "size_mb": 100,
            "sha256": "abc123",
        }
        with patch("tinyagentos.installers.download_installer.download_file", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tmp_path / "qwen3-8b-q4_k_m.gguf"
            result = await installer.install("qwen3-8b", {"method": "download"}, variant=variant)
            assert result["success"] is True
            mock_dl.assert_called_once()
