import io, zipfile
import pytest
from unittest.mock import AsyncMock, patch

CONTAINER_MANIFEST = (
    "id: echo\nname: Echo\nversion: 1.0.0\napp_type: container\n"
    "entry: index.html\nicon: icon.png\npermissions: []\n"
    "container:\n  image: docker.io/hashicorp/http-echo:latest\n  ports: [5678]\n"
)

def _zip(manifest=CONTAINER_MANIFEST):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("index.html", "x"); z.writestr("icon.png", "x")
    return buf.getvalue()

@pytest.mark.asyncio
async def test_container_app_deploys_backend_on_install(client):
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        r = await client.post("/api/userspace-apps/install",
                              files={"package": ("echo.taosapp", _zip(), "application/zip")})
        assert r.status_code == 200
        deploy.assert_awaited_once()
        assert "http-echo" in str(deploy.await_args)   # image reached the deploy call
        assert r.json()["container_deployed"] is True
    # runtime location was recorded
    apps = (await client.get("/api/userspace-apps")).json()
    echo = next(a for a in apps if a["app_id"] == "echo")
    assert echo["container_host"] == "127.0.0.1" and echo["container_port"] == 13042

@pytest.mark.asyncio
async def test_web_app_does_not_deploy_a_container(client):
    web = "id: w\nname: W\nversion: 1\napp_type: web\nentry: index.html\nicon: i\npermissions: []\n"
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        r = await client.post("/api/userspace-apps/install",
                              files={"package": ("w.taosapp", _zip(web), "application/zip")})
        assert r.status_code == 200
        deploy.assert_not_awaited()

@pytest.mark.asyncio
async def test_uninstall_destroys_container_app_backend(client):
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy, \
         patch("tinyagentos.routes.userspace_apps.destroy_app_container",
               new_callable=AsyncMock) as destroy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        await client.post("/api/userspace-apps/install",
                          files={"package": ("echo.taosapp", _zip(), "application/zip")})
        await client.delete("/api/userspace-apps/echo")
        destroy.assert_awaited_once()
        assert destroy.await_args.args[0] == "echo"

@pytest.mark.asyncio
async def test_reinstall_tears_down_old_container_first(client):
    """Re-installing a container app must call destroy before deploy on the second install."""
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy, \
         patch("tinyagentos.routes.userspace_apps.destroy_app_container",
               new_callable=AsyncMock) as destroy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}

        # First install — no prior app, destroy must NOT be called.
        r1 = await client.post("/api/userspace-apps/install",
                               files={"package": ("echo.taosapp", _zip(), "application/zip")})
        assert r1.status_code == 200
        destroy.assert_not_awaited()

        # Second install of the same id — destroy must be called once with "echo"
        # BEFORE deploy is called again.
        deploy_call_count_before = deploy.await_count
        r2 = await client.post("/api/userspace-apps/install",
                               files={"package": ("echo.taosapp", _zip(), "application/zip")})
        assert r2.status_code == 200
        destroy.assert_awaited_once()
        assert destroy.await_args.args[0] == "echo"
        # deploy was called a second time and succeeded
        assert deploy.await_count == deploy_call_count_before + 1
        assert r2.json()["container_deployed"] is True


@pytest.mark.asyncio
async def test_deploy_failure_still_installs_but_reports_error(client):
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": False, "error": "image not found"}
        r = await client.post("/api/userspace-apps/install",
                              files={"package": ("echo.taosapp", _zip(), "application/zip")})
        assert r.status_code == 200
        body = r.json()
        assert body["container_deployed"] is False
        assert body["deploy_error"] == "image not found"
        # app is still registered
        apps = (await client.get("/api/userspace-apps")).json()
        assert any(a["app_id"] == "echo" for a in apps)
