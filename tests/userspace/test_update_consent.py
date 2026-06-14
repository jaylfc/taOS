# tests/userspace/test_update_consent.py — install v1 (app.net), then "update" requesting app.memory too
import io, zipfile
import pytest

def _zip(perms):
    m = f"id: todo\nname: Todo\nversion: 1.0.0\napp_type: web\nentry: index.html\nicon: icon.png\npermissions: {perms}\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", m); z.writestr("index.html", "x"); z.writestr("icon.png", "x")
    return buf.getvalue()

@pytest.mark.asyncio
async def test_update_with_new_permission_flags_consent(client):
    await client.post("/api/userspace-apps/install", files={"package": ("t.taosapp", _zip("[app.net]"), "application/zip")})
    await client.post("/api/userspace-apps/todo/permissions", json={"granted": ["app.net"]})
    r = await client.post("/api/userspace-apps/install", files={"package": ("t.taosapp", _zip("[app.net, app.memory]"), "application/zip")})
    body = r.json()
    assert body["needs_consent"] is True
    assert "app.memory" in body["new_permissions"]
