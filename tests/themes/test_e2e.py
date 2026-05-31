# tests/themes/test_e2e.py
import io, zipfile, yaml, pytest

def _zip(m):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z: z.writestr("theme.yaml", yaml.safe_dump(m))
    return b.getvalue()

@pytest.mark.asyncio
async def test_install_use_remove(client):
    m = {"id":"matrix","name":"Matrix","version":"1.0.0","tokens":{"--color-accent":"#00ff46"},
         "structure":{"dock":{"variant":"windows-taskbar"}},"effects":[{"module":"crt"}],
         "requires":["assistant","launcher"]}
    await client.post("/api/themes/install", files={"package":("m.taostheme", _zip(m), "application/zip")})
    got = next(t for t in (await client.get("/api/themes")).json() if t["theme_id"]=="matrix")
    assert got["config"]["structure"]["dock"]["variant"] == "windows-taskbar"
    await client.delete("/api/themes/matrix")
    assert all(t["theme_id"]!="matrix" for t in (await client.get("/api/themes")).json())
