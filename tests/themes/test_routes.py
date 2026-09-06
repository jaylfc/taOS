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


def _zip_with_asset(m, asset_path, asset_bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.yaml", yaml.safe_dump(m))
        z.writestr(asset_path, asset_bytes)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_theme_asset_served_but_traversal_blocked(client):
    """Assets are served, but neither a non-slug theme_id nor a ../ in the
    asset path can escape the theme's assets dir (path-traversal guard)."""
    pkg = _zip_with_asset(M, "assets/logo.txt", b"PIXELS")
    r = await client.post("/api/themes/install",
                          files={"package": ("matrix.taostheme", pkg, "application/zip")})
    assert r.status_code == 200

    # Positive: a legitimate asset is served.
    ok = await client.get("/api/themes/matrix/assets/logo.txt")
    assert ok.status_code == 200 and ok.content == b"PIXELS"

    # A non-slug theme_id (would relocate the assets root) is rejected.
    bad_id = await client.get("/api/themes/matrix.evil/assets/logo.txt")
    assert bad_id.status_code == 404

    # A ../ in the asset path (percent-encoded so the client doesn't
    # normalise it away) must not escape the assets dir — theme.yaml lives
    # one level up in the theme dir, so a successful escape would leak it.
    escape = await client.get("/api/themes/matrix/assets/%2e%2e/theme.yaml")
    assert escape.status_code == 404
