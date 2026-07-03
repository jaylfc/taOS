import io, zipfile
import pytest
from unittest.mock import AsyncMock, Mock, patch
import httpx

WEB_MANIFEST = "id: todo\nname: Todo\nversion: 1.0.0\napp_type: web\nentry: index.html\nicon: icon.png\npermissions: [app.net]\n"


def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", WEB_MANIFEST)
        z.writestr("index.html", "<h1>todo</h1>")
        z.writestr("icon.png", "x")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_install_list_bundle_uninstall(client):
    r = await client.post("/api/userspace-apps/install",
                          files={"package": ("todo.taosapp", _zip(), "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["app_id"] == "todo"
    assert r.json()["permissions_requested"] == ["app.net"]

    r = await client.get("/api/userspace-apps")
    assert any(a["app_id"] == "todo" for a in r.json())

    r = await client.get("/api/userspace-apps/todo/bundle/index.html")
    assert r.status_code == 200
    assert "todo" in r.text
    csp = r.headers.get("content-security-policy", "").lower()
    assert "frame-ancestors" in csp or "default-src" in csp

    r = await client.delete("/api/userspace-apps/todo")
    assert r.status_code == 200
    rows = (await client.get("/api/userspace-apps")).json()
    assert all(a["app_id"] != "todo" for a in rows)


@pytest.mark.asyncio
async def test_bundle_path_traversal_404(client):
    await client.post("/api/userspace-apps/install",
                      files={"package": ("todo.taosapp", _zip(), "application/zip")})
    # Use percent-encoded traversal so URL normalization cannot collapse it
    # before the route handler, ensuring the bundle path guard is exercised.
    r = await client.get("/api/userspace-apps/todo/bundle/%2e%2e%2f%2e%2e%2fetc%2fpasswd")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_install_malformed_json_returns_400(client):
    # Finding 2: a request body that is not valid JSON must return 400, not 500.
    r = await client.post(
        "/api/userspace-apps/install",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert "invalid" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_install_upstream_fetch_failure_returns_502(client):
    # Finding 2: httpx connectivity failure on source_url must return 502.
    with patch("tinyagentos.routes.userspace_apps.resolve_safe_public_ip",
               return_value="93.184.216.34"):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock,
                   side_effect=httpx.ConnectError("refused")):
            r = await client.post(
                "/api/userspace-apps/install",
                json={"source_url": "http://example.com/app.taosapp"},
            )
    assert r.status_code == 502
    assert "upstream" in r.json()["error"].lower() or "fetch" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_install_pins_connection_to_resolved_ip(client):
    # SSRF #971: the fetch must connect to the validated IP (closing the
    # DNS-rebind TOCTOU window), keeping the original Host header + TLS SNI so
    # the client never re-resolves the hostname to a different (internal) IP.
    captured = {}

    async def _fake_get(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["extensions"] = kwargs.get("extensions")
        resp = Mock()
        resp.raise_for_status = Mock(return_value=None)
        resp.content = _zip()
        return resp

    with patch("tinyagentos.routes.userspace_apps.resolve_safe_public_ip",
               return_value="93.184.216.34"):
        with patch("httpx.AsyncClient.get", new=_fake_get):
            r = await client.post(
                "/api/userspace-apps/install",
                json={"source_url": "http://example.com/app.taosapp"},
            )
    assert r.status_code == 200, r.text
    # connected to the pinned IP, NOT the hostname
    assert "93.184.216.34" in captured["url"]
    assert "example.com" not in captured["url"]
    # original host preserved for vhost routing + cert validation
    assert captured["headers"]["Host"] == "example.com"
    assert captured["extensions"]["sni_hostname"] == "example.com"


def _container_zip():
    manifest = (
        "id: ctapp\nname: ContainerApp\nversion: 1.0.0\napp_type: container\n"
        "entry: index.html\nicon: ''\npermissions: []\n"
        "container:\n  image: myimage:latest\n  ports: [8080]\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("index.html", "<h1>ct</h1>")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_container_install_no_longer_gated(client):
    # App Runtime M4: container packages are no longer rejected at install --
    # see tests/userspace/test_container_app.py for the full deploy/enable/
    # disable/uninstall lifecycle. This just confirms the old 501 gate is gone
    # and the app is stored (not auto-deployed at install time).
    r = await client.post(
        "/api/userspace-apps/install",
        files={"package": ("ctapp.taosapp", _container_zip(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["app_id"] == "ctapp"

    rows = (await client.get("/api/userspace-apps")).json()
    row = next(a for a in rows if a["app_id"] == "ctapp")
    assert row["app_type"] == "container"
    assert row["container_host"] is None and row["container_port"] is None


def _net_zip():
    manifest = ("id: net\nname: Net\nversion: 1.0.0\napp_type: web\nentry: index.html\n"
                "icon: icon.png\npermissions:\n  - 'network:wss://irc-ws.chat.twitch.tv'\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("index.html", "<h1>net</h1>")
        z.writestr("icon.png", "x")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_bundle_csp_reflects_granted_network_permission(client):
    await client.post("/api/userspace-apps/install",
                      files={"package": ("net.taosapp", _net_zip(), "application/zip")})
    # Before granting, connect-src stays locked to 'self'.
    r = await client.get("/api/userspace-apps/net/bundle/index.html")
    csp = r.headers["content-security-policy"]
    assert "connect-src 'self';" in csp and "twitch" not in csp
    # Grant the declared network permission -> the origin appears in connect-src.
    await client.post("/api/userspace-apps/net/permissions",
                      json={"granted": ["network:wss://irc-ws.chat.twitch.tv"]})
    r = await client.get("/api/userspace-apps/net/bundle/index.html")
    csp = r.headers["content-security-policy"]
    assert "connect-src 'self' wss://irc-ws.chat.twitch.tv;" in csp
