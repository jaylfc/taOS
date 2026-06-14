# tests/userspace/test_e2e.py
import io, zipfile
import pytest

def _todo_zip():
    manifest = "id: todo\nname: Todo\nversion: 1.0.0\napp_type: web\nentry: index.html\nicon: icon.png\npermissions: []\n"
    html = '<script src="/api/userspace-apps/sdk.js?app=todo"></script>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest); z.writestr("index.html", html); z.writestr("icon.png", "x")
    return buf.getvalue()

@pytest.mark.asyncio
async def test_install_use_isolate_uninstall(client, tmp_path):
    await client.post("/api/userspace-apps/install", files={"package": ("todo.taosapp", _todo_zip(), "application/zip")})
    # use via broker
    await client.post("/api/userspace-apps/todo/broker",
                      json={"capability": "app.table.insert", "args": {"table": "t", "row": {"text": "milk"}}})
    rows = (await client.post("/api/userspace-apps/todo/broker",
            json={"capability": "app.table.query", "args": {"table": "t"}})).json()["result"]
    assert len(rows) == 1 and rows[0]["text"] == "milk"
    # uninstall removes registry + data
    await client.delete("/api/userspace-apps/todo")
    assert all(a["app_id"] != "todo" for a in (await client.get("/api/userspace-apps")).json())
    after = (await client.post("/api/userspace-apps/todo/broker",
             json={"capability": "app.table.query", "args": {"table": "t"}}))
    assert after.status_code == 404  # app gone
