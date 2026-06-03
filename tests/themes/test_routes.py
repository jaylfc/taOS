import io, zipfile, yaml, pytest

def _zip(m):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.yaml", yaml.safe_dump(m))
    return buf.getvalue()

M = {"id": "matrix", "name": "Matrix", "version": "1.0.0",
     "tokens": {"--color-accent": "#00ff46"}, "structure": {}, "effects": [],
     "requires": ["assistant", "launcher"]}

@pytest.mark.asyncio
async def test_install_then_list(client):
    r = await client.post("/api/themes/install",
                          files={"package": ("matrix.taostheme", _zip(M), "application/zip")})
    assert r.status_code == 200 and r.json()["theme_id"] == "matrix"
    rows = (await client.get("/api/themes")).json()
    assert any(t["theme_id"] == "matrix" for t in rows)

@pytest.mark.asyncio
async def test_install_rejects_bad_config(client):
    bad = dict(M); bad["tokens"] = {"--evil": "x"}
    r = await client.post("/api/themes/install",
                          files={"package": ("b.taostheme", _zip(bad), "application/zip")})
    assert r.status_code == 400
