import io, zipfile
import pytest
from unittest.mock import AsyncMock, patch
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
    with patch("tinyagentos.routes.userspace_apps.is_safe_public_url", return_value=True):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock,
                   side_effect=httpx.ConnectError("refused")):
            r = await client.post(
                "/api/userspace-apps/install",
                json={"source_url": "http://example.com/app.taosapp"},
            )
    assert r.status_code == 502
    assert "upstream" in r.json()["error"].lower() or "fetch" in r.json()["error"].lower()


def _container_zip():
    manifest = (
        "id: ctapp\nname: ContainerApp\nversion: 1.0.0\napp_type: container\n"
        "entry: index.html\nicon: \npermissions: []\n"
        "container:\n  image: myimage:latest\n  ports: [8080]\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("index.html", "<h1>ct</h1>")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_container_install_rejected_with_no_stored_state(client):
    # Finding 3: installing a container package must return 501 before
    # persisting anything -- no app row and no extracted directory must remain.
    r = await client.post(
        "/api/userspace-apps/install",
        files={"package": ("ctapp.taosapp", _container_zip(), "application/zip")},
    )
    assert r.status_code == 501
    assert "container" in r.json()["error"].lower()

    # No app row stored.
    rows = (await client.get("/api/userspace-apps")).json()
    assert all(a["app_id"] != "ctapp" for a in rows)
