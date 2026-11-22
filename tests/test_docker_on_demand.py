"""Tests for Docker-on-demand install flow."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.routes.store_install import (
    _docker_available,
    _get_app_runtime,
    _legacy_install,
)
from tinyagentos.docker_setup import ensure_docker_on_demand


class TestManifestRuntimeField:
    """Test that all catalog apps have the runtime field."""

    def test_all_manifests_have_runtime(self):
        """Verify every manifest.yaml in app-catalog has a runtime field."""
        import yaml
        from pathlib import Path

        catalog_dir = Path(__file__).parents[1] / "app-catalog"
        manifest_files = list(catalog_dir.rglob("manifest.yaml"))
        mcp_files = list((catalog_dir / "mcp-registry").glob("*.yaml"))

        missing_runtime = []
        for mf in manifest_files + mcp_files:
            with open(mf) as f:
                data = yaml.safe_load(f)
            
            # Check for runtime in install block or top-level
            install = data.get("install", {})
            has_runtime = False
            if isinstance(install, dict) and "runtime" in install:
                has_runtime = True
            elif "runtime" in data:
                has_runtime = True
            
            if not has_runtime:
                missing_runtime.append(str(mf))

        assert not missing_runtime, f"Manifests missing runtime field: {missing_runtime}"

    def test_runtime_values_are_valid(self):
        """Verify runtime values are one of docker, lxc, either."""
        import yaml
        from pathlib import Path

        catalog_dir = Path(__file__).parents[1] / "app-catalog"
        manifest_files = list(catalog_dir.rglob("manifest.yaml"))
        mcp_files = list((catalog_dir / "mcp-registry").glob("*.yaml"))

        invalid_runtime = []
        valid_values = {"docker", "lxc", "either"}
        for mf in manifest_files + mcp_files:
            with open(mf) as f:
                data = yaml.safe_load(f)
            
            install = data.get("install", {})
            runtime = None
            if isinstance(install, dict) and "runtime" in install:
                runtime = install["runtime"]
            elif "runtime" in data:
                runtime = data["runtime"]
            
            if runtime and runtime not in valid_values:
                invalid_runtime.append(f"{mf}: runtime={runtime}")

        assert not invalid_runtime, f"Manifests with invalid runtime values: {invalid_runtime}"


class TestDockerAvailableCheck:
    """Test the _docker_available helper function."""

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_docker_available_when_installed_and_running(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0)
        
        assert _docker_available() is True
        mock_which.assert_called_with("docker")
        mock_run.assert_called_once()

    @patch("shutil.which")
    def test_docker_not_available_when_not_installed(self, mock_which):
        mock_which.return_value = None
        
        assert _docker_available() is False

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_docker_not_available_when_daemon_down(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=1)
        
        assert _docker_available() is False


class TestGetAppRuntime:
    """Test the _get_app_runtime helper function."""

    def test_docker_runtime_from_install_block(self):
        manifest = MagicMock()
        manifest.install = {"method": "docker", "runtime": "docker"}
        assert _get_app_runtime(manifest) == "docker"

    def test_lxc_runtime_from_install_block(self):
        manifest = MagicMock()
        manifest.install = {"method": "lxc", "runtime": "lxc"}
        assert _get_app_runtime(manifest) == "lxc"

    def test_either_runtime_from_install_block(self):
        manifest = MagicMock()
        manifest.install = {"method": "script", "runtime": "either"}
        assert _get_app_runtime(manifest) == "either"

    def test_runtime_from_top_level_for_models(self):
        manifest = MagicMock()
        manifest.install = None
        manifest.runtime = "either"
        assert _get_app_runtime(manifest) == "either"

    def test_default_either_when_missing(self):
        manifest = MagicMock()
        manifest.install = {}
        del manifest.runtime
        assert _get_app_runtime(manifest) == "either"


class TestLegacyInstallDockerRequired:
    """Test _legacy_install returns docker_required when Docker is needed but not available."""

    def _make_manifest(self, install_dict, manifest_id, manifest_type="service", version="1.0.0"):
        """Create a simple manifest object with the given install dict."""
        class SimpleManifest:
            def __init__(self, install, id, type, version, variants=None):
                self.install = install
                self.id = id
                self.type = type
                self.version = version
                self.variants = variants or []
        
        return SimpleManifest(install_dict, manifest_id, manifest_type, version)

    @pytest.mark.asyncio
    async def test_docker_required_returned_when_docker_missing(self):
        """Installing a docker-runtime app without Docker returns docker_required."""
        request = MagicMock()
        request.app.state.registry = MagicMock()  # Must not be None for _registry_get to be called
        request.app.state.installed_apps = None
        
        # Mock manifest with docker runtime
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest", "ports": [8080]}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False):
                body = {"metadata": {}}
                response = await _legacy_install(request, body, "test-app", None)
                
                assert response.status_code == 412
                data = response.body.decode()
                assert "docker_required" in data
                assert "test-app" in data

    @pytest.mark.asyncio
    async def test_docker_not_required_for_lxc_apps(self):
        """LXC apps never trigger the Docker check."""
        request = MagicMock()
        request.app.state.registry = MagicMock()
        request.app.state.installed_apps = None
        
        install_dict = {"method": "lxc", "runtime": "lxc", "image": "images:debian/bookworm", "memory_limit": "512MiB", "cpu_limit": 1}
        manifest = self._make_manifest(install_dict, "test-lxc")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False) as mock_docker:
                with patch("tinyagentos.routes.store_install.LXCInstaller") as mock_lxc_installer_class:
                    mock_lxc_installer = AsyncMock()
                    mock_lxc_installer.install.return_value = {"success": True, "host_port": 30000, "container": "taos-svc-test-lxc"}
                    mock_lxc_installer_class.return_value = mock_lxc_installer
                    
                    body = {"metadata": {}, "admin_password": "test123"}
                    response = await _legacy_install(request, body, "test-lxc", None)
                    
                    # Should not have called _docker_available for LXC app
                    # (The check only happens for backend == "docker")
                    assert response.status_code != 412

    @pytest.mark.asyncio
    async def test_docker_not_required_for_script_apps(self):
        """Script apps never trigger the Docker check."""
        request = MagicMock()
        request.app.state.registry = MagicMock()
        request.app.state.installed_apps = None
        
        install_dict = {"method": "script", "runtime": "either", "script": "scripts/install.sh"}
        manifest = self._make_manifest(install_dict, "test-script")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False) as mock_docker:
                with patch("tinyagentos.installers.script_installer.ScriptInstaller") as mock_script_installer_class:
                    mock_script_installer = AsyncMock()
                    mock_script_installer.install.return_value = {"success": True}
                    mock_script_installer_class.return_value = mock_script_installer
                    
                    body = {"metadata": {}}
                    response = await _legacy_install(request, body, "test-script", None)
                    
                    # Should proceed to script installer, not docker check
                    assert response.status_code != 412


class TestDockerConsentFlow:
    """Test the Docker consent flow."""

    def _make_manifest(self, install_dict, manifest_id, manifest_type="service", version="1.0.0"):
        """Create a simple manifest object with the given install dict."""
        class SimpleManifest:
            def __init__(self, install, id, type, version, variants=None):
                self.install = install
                self.id = id
                self.type = type
                self.version = version
                self.variants = variants or []
        
        return SimpleManifest(install_dict, manifest_id, manifest_type, version)

    def _make_request(self, *, via: str = "session", is_admin: bool = True):
        """Create a mock request with the given auth state."""
        request = MagicMock()
        request.app.state.registry = MagicMock()
        request.app.state.installed_apps = AsyncMock()
        request.app.state.apps_dir = None
        request.app.state.data_dir = "/tmp/test-data"
        request.state.via = via
        request.state.is_admin = is_admin
        return request

    @pytest.mark.asyncio
    async def test_consent_proceeds_with_docker_install(self):
        """When docker_consent=true from a human admin session, Docker is installed and app install proceeds."""
        request = self._make_request(via="session", is_admin=True)
        
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest", "ports": [8080]}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", side_effect=[False, True]):
                with patch("tinyagentos.docker_setup.ensure_docker_on_demand", new_callable=AsyncMock) as mock_ensure:
                    mock_ensure.return_value = {"success": True}
                    with patch("tinyagentos.installers.docker_installer.DockerInstaller") as mock_installer_class:
                        mock_installer = AsyncMock()
                        mock_installer.install.return_value = {"success": True, "host_port": 30000}
                        mock_installer.start.return_value = {"success": True}
                        mock_installer_class.return_value = mock_installer
                        
                        body = {"metadata": {}, "docker_consent": True}
                        response = await _legacy_install(request, body, "test-app", None)
                        
                        assert response.status_code == 200
                        mock_ensure.assert_called_once()
                        mock_installer.install.assert_called_once()

    @pytest.mark.asyncio
    async def test_docker_install_failure_returns_error(self):
        """If Docker installation fails, return error."""
        request = self._make_request(via="session", is_admin=True)
        
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest"}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False):
                with patch("tinyagentos.docker_setup.ensure_docker_on_demand", new_callable=AsyncMock) as mock_ensure:
                    mock_ensure.return_value = {"success": False, "error": "apt install failed"}
                    
                    body = {"metadata": {}, "docker_consent": True}
                    response = await _legacy_install(request, body, "test-app", None)
                    
                    assert response.status_code == 500
                    data = response.body.decode()
                    assert "Docker installation failed" in data

    @pytest.mark.asyncio
    async def test_agent_token_cannot_consent(self):
        """Agent-bound token (via=registry_jwt) gets 403 even with docker_consent=true."""
        request = self._make_request(via="registry_jwt", is_admin=True)
        
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest"}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False):
                body = {"metadata": {}, "docker_consent": True}
                response = await _legacy_install(request, body, "test-app", None)
                
                assert response.status_code == 403
                data = response.body.decode()
                assert "signed-in human admin session" in data.lower()

    @pytest.mark.asyncio
    async def test_local_token_cannot_consent(self):
        """Host local token (via=local_token) gets 403 even with docker_consent=true."""
        request = self._make_request(via="local_token", is_admin=True)
        
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest"}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False):
                body = {"metadata": {}, "docker_consent": True}
                response = await _legacy_install(request, body, "test-app", None)
                
                assert response.status_code == 403
                data = response.body.decode()
                assert "signed-in human admin session" in data.lower()

    @pytest.mark.asyncio
    async def test_non_admin_session_cannot_consent(self):
        """Non-admin human session (via=session, is_admin=False) gets 403."""
        request = self._make_request(via="session", is_admin=False)
        
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest"}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False):
                body = {"metadata": {}, "docker_consent": True}
                response = await _legacy_install(request, body, "test-app", None)
                
                assert response.status_code == 403
                data = response.body.decode()
                assert "requires admin privileges" in data.lower()

    @pytest.mark.asyncio
    async def test_anonymous_cannot_consent(self):
        """Anonymous request (no via, no is_admin) gets 403."""
        request = self._make_request(via=None, is_admin=False)
        
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest"}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=False):
                body = {"metadata": {}, "docker_consent": True}
                response = await _legacy_install(request, body, "test-app", None)
                
                assert response.status_code == 403
                data = response.body.decode()
                assert "signed-in human admin session" in data.lower()


    @pytest.mark.asyncio
    async def test_docker_already_installed_proceeds_without_consent(self):
        """When Docker is already installed, install proceeds without requiring consent."""
        request = self._make_request(via="session", is_admin=True)
        
        install_dict = {"method": "docker", "runtime": "docker", "image": "test:latest", "ports": [8080]}
        manifest = self._make_manifest(install_dict, "test-app")
        
        with patch("tinyagentos.routes.store_install._registry_get", return_value=manifest):
            with patch("tinyagentos.routes.store_install._docker_available", return_value=True):
                with patch("tinyagentos.installers.docker_installer.DockerInstaller") as mock_installer_class:
                    mock_installer = AsyncMock()
                    mock_installer.install.return_value = {"success": True, "host_port": 30000}
                    mock_installer.start.return_value = {"success": True}
                    mock_installer_class.return_value = mock_installer
                    
                    body = {"metadata": {}}  # No docker_consent needed
                    response = await _legacy_install(request, body, "test-app", None)
                    
                    assert response.status_code == 200
                    mock_installer.install.assert_called_once()
                    mock_installer.start.assert_called_once()


class TestEnsureDockerOnDemand:
    """Test the ensure_docker_on_demand function."""

    @pytest.mark.asyncio
    @patch("shutil.which")
    async def test_ensure_docker_on_demand_already_installed(self, mock_which):
        """Test when Docker is already installed and running."""
        mock_which.return_value = "/usr/bin/docker"
        
        with patch("tinyagentos.docker_setup._docker_running", return_value=True):
            result = await ensure_docker_on_demand()
            
            assert result["success"] is True

    @pytest.mark.asyncio
    @patch("shutil.which")
    @patch.dict("os.environ", {"TAOS_SKIP_DOCKER": "0"})
    async def test_ensure_docker_on_demand_not_installed(self, mock_which):
        """Test when Docker is not installed - returns docker_required with command."""
        mock_which.return_value = None
        
        with patch("tinyagentos.docker_setup._docker_running", return_value=False):
            result = await ensure_docker_on_demand()
            
            assert result.get("docker_required") is True
            assert "command" in result
            assert "message" in result
            assert "sudo" in result["command"]

    @pytest.mark.asyncio
    @patch("shutil.which")
    @patch.dict("os.environ", {"TAOS_SKIP_DOCKER": "1"})
    async def test_ensure_docker_on_demand_skip_docker(self, mock_which):
        """Test TAOS_SKIP_DOCKER=1 returns docker_required with empty command."""
        mock_which.return_value = None
        
        with patch("tinyagentos.docker_setup._docker_running", return_value=False):
            result = await ensure_docker_on_demand()
            
            assert result.get("docker_required") is True
            assert result["command"] == ""
            assert "TAOS_SKIP_DOCKER=1" in result["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])