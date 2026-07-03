"""App Runtime M4: container app lifecycle -- deploy on enable, destroy on
disable/uninstall, and the enable/disable route contracts around it.

deploy_app_container / destroy_app_container are patched at the point they
are imported into tinyagentos.routes.userspace_apps so these tests never
touch a real Docker daemon.
"""
import io
import zipfile

import pytest
from unittest.mock import AsyncMock, patch

CONTAINER_MANIFEST = (
    "id: echo\nname: Echo\nversion: 1.0.0\napp_type: container\n"
    "entry: index.html\nicon: ''\npermissions: []\n"
    "container:\n  image: docker.io/hashicorp/http-echo:latest\n  ports: [5678]\n"
)


def _zip(manifest=CONTAINER_MANIFEST):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("index.html", "x")
    return buf.getvalue()


async def _install(client):
    r = await client.post(
        "/api/userspace-apps/install",
        files={"package": ("echo.taosapp", _zip(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    return r


@pytest.mark.asyncio
async def test_install_does_not_deploy_a_container(client):
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        await _install(client)
        deploy.assert_not_awaited()
    apps = (await client.get("/api/userspace-apps")).json()
    echo = next(a for a in apps if a["app_id"] == "echo")
    assert echo["container_host"] is None and echo["container_port"] is None


@pytest.mark.asyncio
async def test_enable_deploys_backend_and_records_runtime_location(client):
    await _install(client)
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        r = await client.post("/api/userspace-apps/echo/enable")
        assert r.status_code == 200, r.text
        deploy.assert_awaited_once()
        # the image from the manifest's container block reached deploy_app_container
        assert "http-echo" in str(deploy.await_args)

    apps = (await client.get("/api/userspace-apps")).json()
    echo = next(a for a in apps if a["app_id"] == "echo")
    assert echo["container_host"] == "127.0.0.1" and echo["container_port"] == 13042
    assert echo["enabled"]


@pytest.mark.asyncio
async def test_enable_failure_keeps_app_disabled_and_reports_error(client):
    await _install(client)
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": False, "error": "image not found"}
        r = await client.post("/api/userspace-apps/echo/enable")
        assert r.status_code == 502
        assert r.json()["error"] == "image not found"

    apps = (await client.get("/api/userspace-apps")).json()
    echo = next(a for a in apps if a["app_id"] == "echo")
    # Not left half-enabled: enabled flips back off, no runtime location.
    assert not echo["enabled"]
    assert echo["container_host"] is None and echo["container_port"] is None


@pytest.mark.asyncio
async def test_docker_missing_is_graceful_on_enable(client):
    await _install(client)
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {
            "success": False,
            "error": "Docker is required to run container apps but was not found.",
        }
        r = await client.post("/api/userspace-apps/echo/enable")
        assert r.status_code == 502
        assert "Docker" in r.json()["error"]
    apps = (await client.get("/api/userspace-apps")).json()
    assert not next(a for a in apps if a["app_id"] == "echo")["enabled"]


@pytest.mark.asyncio
async def test_disable_destroys_backend_and_clears_runtime_location(client):
    await _install(client)
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        await client.post("/api/userspace-apps/echo/enable")

    with patch("tinyagentos.routes.userspace_apps.destroy_app_container",
               new_callable=AsyncMock) as destroy:
        r = await client.post("/api/userspace-apps/echo/disable")
        assert r.status_code == 200
        destroy.assert_awaited_once()
        assert destroy.await_args.args[0] == "echo"

    apps = (await client.get("/api/userspace-apps")).json()
    echo = next(a for a in apps if a["app_id"] == "echo")
    assert not echo["enabled"]
    assert echo["container_host"] is None and echo["container_port"] is None


@pytest.mark.asyncio
async def test_uninstall_destroys_container_app_backend_first(client):
    await _install(client)
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        await client.post("/api/userspace-apps/echo/enable")

    with patch("tinyagentos.routes.userspace_apps.destroy_app_container",
               new_callable=AsyncMock) as destroy:
        r = await client.delete("/api/userspace-apps/echo")
        assert r.status_code == 200
        destroy.assert_awaited_once()
        assert destroy.await_args.args[0] == "echo"

    apps = (await client.get("/api/userspace-apps")).json()
    assert all(a["app_id"] != "echo" for a in apps)


@pytest.mark.asyncio
async def test_web_app_enable_never_touches_container_deploy(client):
    web = "id: w\nname: W\nversion: 1\napp_type: web\nentry: index.html\nicon: ''\npermissions: []\n"
    r = await client.post("/api/userspace-apps/install",
                          files={"package": ("w.taosapp", _zip(web), "application/zip")})
    assert r.status_code == 200
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        r = await client.post("/api/userspace-apps/w/enable")
        assert r.status_code == 200
        deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxy_forwards_only_to_recorded_runtime_location(client):
    await _install(client)
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        await client.post("/api/userspace-apps/echo/enable")

    captured = {}

    class _FakeUpstreamResponse:
        status_code = 200
        headers = {"content-type": "text/plain"}

        async def aiter_bytes(self):
            yield b"hello from echo"

        async def aclose(self):
            pass

    async def _fake_send(req, **kwargs):
        captured["url"] = str(req.url)
        return _FakeUpstreamResponse()

    # Patch only the proxy's own client instance -- patching AsyncClient.send
    # on the class would also intercept the test client's ASGI requests.
    with patch("tinyagentos.routes.userspace_apps._container_proxy_client.send", new=_fake_send):
        r = await client.get("/api/userspace-apps/echo/proxy/status")

    assert r.status_code == 200
    assert r.content == b"hello from echo"
    # the proxy only ever targets the exact recorded 127.0.0.1:<port> location
    assert captured["url"] == "http://127.0.0.1:13042/status"
    # sandbox CSP is applied to the proxied response too
    assert "sandbox" in r.headers.get("content-security-policy", "").lower()


@pytest.mark.asyncio
async def test_proxy_404s_when_no_runtime_location(client):
    await _install(client)  # installed, but never enabled -- no runtime location
    r = await client.get("/api/userspace-apps/echo/proxy/")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_proxy_strips_taos_session_even_from_malformed_cookie(client):
    # A malformed Cookie header must NOT leak the taos_session credential to
    # the untrusted container backend, even when it is not valid cookie
    # grammar (the scrub is textual, never SimpleCookie-dependent).
    await _install(client)
    with patch("tinyagentos.routes.userspace_apps.deploy_app_container",
               new_callable=AsyncMock) as deploy:
        deploy.return_value = {"success": True, "host": "127.0.0.1", "port": 13042}
        await client.post("/api/userspace-apps/echo/enable")

    captured = {}

    class _FakeUpstreamResponse:
        status_code = 200
        headers = {"content-type": "text/plain"}

        async def aiter_bytes(self):
            yield b"ok"

        async def aclose(self):
            pass

    async def _fake_send(req, **kwargs):
        captured["cookie"] = req.headers.get("cookie", "")
        return _FakeUpstreamResponse()

    # Include the REAL session cookie (so auth passes) inside an otherwise
    # malformed Cookie header that would trip SimpleCookie.load. taos_session
    # is exactly what must be scrubbed before the request reaches the backend.
    session_token = client.cookies.get("taos_session")
    malformed = f"taos_session={session_token}; bad==value; keep=1; ;;"
    with patch("tinyagentos.routes.userspace_apps._container_proxy_client.send", new=_fake_send):
        r = await client.get(
            "/api/userspace-apps/echo/proxy/status",
            headers={"Cookie": malformed},
        )

    assert r.status_code == 200
    # taos_session (and its value) must never reach the backend...
    assert "taos_session" not in captured["cookie"]
    assert session_token not in captured["cookie"]
    # ...while an unrelated cookie is still forwarded.
    assert "keep=1" in captured["cookie"]
