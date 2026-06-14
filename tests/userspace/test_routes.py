import io, zipfile
import pytest

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
    r = await client.get("/api/userspace-apps/todo/bundle/../../../etc/passwd")
    assert r.status_code == 404
