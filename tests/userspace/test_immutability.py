# tests/userspace/test_immutability.py
import io, zipfile
import pytest
from tinyagentos.userspace.package import parse_manifest, PackageError

def test_native_app_type_rejected():
    with pytest.raises(PackageError, match="native"):
        parse_manifest("id: x\nname: X\nversion: 1\napp_type: native\n")

@pytest.mark.asyncio
async def test_bundle_path_traversal_blocked(client):
    m = "id: t\nname: T\nversion: 1\napp_type: web\nentry: index.html\nicon: i\npermissions: []\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", m); z.writestr("index.html", "x")
    await client.post("/api/userspace-apps/install", files={"package": ("t.taosapp", buf.getvalue(), "application/zip")})
    r = await client.get("/api/userspace-apps/t/bundle/../../../etc/passwd")
    assert r.status_code == 404

@pytest.mark.asyncio
async def test_cross_app_data_isolation(client):
    for app in ("a", "b"):
        m = f"id: {app}\nname: {app}\nversion: 1\napp_type: web\nentry: index.html\nicon: i\npermissions: []\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("manifest.yaml", m); z.writestr("index.html", "x")
        await client.post("/api/userspace-apps/install", files={"package": (f"{app}.taosapp", buf.getvalue(), "application/zip")})
    await client.post("/api/userspace-apps/a/broker", json={"capability": "app.kv.set", "args": {"key": "secret", "value": 1}})
    out = (await client.post("/api/userspace-apps/b/broker", json={"capability": "app.kv.get", "args": {"key": "secret"}})).json()
    assert out["result"] is None  # b cannot read a's data
