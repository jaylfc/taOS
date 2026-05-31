import pytest
from unittest.mock import AsyncMock, patch
from tinyagentos.userspace import container_deploy as cd


@pytest.mark.asyncio
async def test_deploy_publishes_first_port_and_returns_location():
    with patch.object(cd.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(cd, "_find_free_port", return_value=13042), \
         patch.object(cd.DockerBackend, "create_container", new_callable=AsyncMock) as create:
        create.return_value = {"success": True, "name": "taos-app-echo"}
        out = await cd.deploy_app_container("echo", {"image": "hashicorp/http-echo:latest", "ports": [5678]})
        assert out == {"success": True, "host": "127.0.0.1", "port": 13042}
        # name + image + (host,container) port tuple reached the backend
        kwargs = create.call_args.kwargs
        args = create.call_args.args
        assert args[0] == "taos-app-echo"
        assert kwargs["image"] == "hashicorp/http-echo:latest"
        assert kwargs["ports"] == [(13042, 5678)]


@pytest.mark.asyncio
async def test_deploy_fails_cleanly_without_docker():
    with patch.object(cd.shutil, "which", return_value=None):
        out = await cd.deploy_app_container("echo", {"image": "x", "ports": [5678]})
        assert out["success"] is False
        assert "Docker" in out["error"]


@pytest.mark.asyncio
async def test_deploy_propagates_backend_error():
    with patch.object(cd.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(cd, "_find_free_port", return_value=13042), \
         patch.object(cd.DockerBackend, "create_container", new_callable=AsyncMock) as create:
        create.return_value = {"success": False, "error": "image not found"}
        out = await cd.deploy_app_container("echo", {"image": "nope", "ports": [5678]})
        assert out["success"] is False and out["error"] == "image not found"


@pytest.mark.asyncio
async def test_destroy_calls_backend_remove():
    with patch.object(cd.DockerBackend, "destroy_container", new_callable=AsyncMock) as rm:
        await cd.destroy_app_container("echo")
        rm.assert_awaited_once()
        assert rm.call_args.args[-1] == "taos-app-echo"
