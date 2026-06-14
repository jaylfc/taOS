import io, types, zipfile
import pytest
from unittest.mock import AsyncMock, patch

MANIFEST = "id: todo\nname: Todo\nversion: 1.0.0\napp_type: web\nentry: index.html\nicon: icon.png\npermissions: [app.memory]\n"

CONTAINER_MANIFEST = (
    "id: echo\nname: Echo\nversion: 1.0.0\napp_type: container\n"
    "entry: index.html\nicon: icon.png\npermissions: [app.net]\n"
    "container:\n  image: docker.io/hashicorp/http-echo:latest\n  ports: [5678]\n"
)


def _container_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", CONTAINER_MANIFEST)
        z.writestr("index.html", "x")
        z.writestr("icon.png", "x")
    return buf.getvalue()

def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", MANIFEST)
        z.writestr("index.html", "<h1>todo</h1>")
        z.writestr("icon.png", "x")
    return buf.getvalue()

@pytest.mark.asyncio
async def test_broker_enforces_granted(client):
    await client.post("/api/userspace-apps/install",
                      files={"package": ("todo.taosapp", _zip(), "application/zip")})
    # free cap works without any grant
    r = await client.post("/api/userspace-apps/todo/broker",
                          json={"capability": "app.kv.set", "args": {"key": "k", "value": 5}})
    assert r.status_code == 200 and r.json()["result"] is True
    r = await client.post("/api/userspace-apps/todo/broker",
                          json={"capability": "app.kv.get", "args": {"key": "k"}})
    assert r.json()["result"] == 5
    # gated cap denied until granted
    r = await client.post("/api/userspace-apps/todo/broker",
                          json={"capability": "app.memory.search", "args": {"q": "x"}})
    assert r.json()["error"] == "permission_denied"
    await client.post("/api/userspace-apps/todo/permissions", json={"granted": ["app.memory"]})
    r = await client.post("/api/userspace-apps/todo/broker",
                          json={"capability": "app.memory.search", "args": {"q": "x"}})
    assert "error" not in r.json()

@pytest.mark.asyncio
async def test_broker_404_for_unknown_app(client):
    r = await client.post("/api/userspace-apps/ghost/broker",
                          json={"capability": "app.kv.get", "args": {"key": "k"}})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_app_net_proxy_works_when_granted_and_backend_recorded(client):
    """Backend proxy forwards the call to the container and returns status+body."""
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        await client.post("/api/userspace-apps/install",
                          files={"package": ("echo.taosapp", _container_zip(), "application/zip")})
    # grant app.net
    await client.post("/api/userspace-apps/echo/permissions", json={"granted": ["app.net"]})

    fake_resp = types.SimpleNamespace(status_code=200, text="pong")
    with patch("tinyagentos.userspace.broker.httpx.AsyncClient") as mock_client:
        mock_request = AsyncMock(return_value=fake_resp)
        mock_client.return_value.__aenter__ = AsyncMock(
            return_value=types.SimpleNamespace(request=mock_request)
        )
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        r = await client.post("/api/userspace-apps/echo/broker",
                              json={"capability": "app.net", "args": {"path": "/ping"}})

    assert r.status_code == 200
    assert r.json() == {"result": {"status": 200, "body": "pong"}}
    called_url = mock_request.call_args[1]["url"] if mock_request.call_args[1].get("url") else mock_request.call_args[0][1]
    assert called_url == "http://127.0.0.1:13042/ping"


@pytest.mark.asyncio
async def test_app_net_denied_without_grant(client):
    """app.net is a gated capability and must be denied if not explicitly granted."""
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        await client.post("/api/userspace-apps/install",
                          files={"package": ("echo.taosapp", _container_zip(), "application/zip")})
    # do NOT grant app.net
    r = await client.post("/api/userspace-apps/echo/broker",
                          json={"capability": "app.net", "args": {"path": "/ping"}})
    assert r.json()["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_app_net_no_backend_returns_error(client):
    """app.net granted on a web app (no container backend) returns no_backend."""
    await client.post("/api/userspace-apps/install",
                      files={"package": ("todo.taosapp", _zip(), "application/zip")})
    await client.post("/api/userspace-apps/todo/permissions", json={"granted": ["app.net"]})
    r = await client.post("/api/userspace-apps/todo/broker",
                          json={"capability": "app.net", "args": {"path": "/ping"}})
    assert r.json() == {"error": "no_backend"}
