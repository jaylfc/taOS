"""Tests for GET /api/coding/workspaces/{id}/preview."""

import base64

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def ws(app, client):
    store = app.state.coding_workspaces
    if store._db is not None:
        await store.close()
    await store.init()

    r = await client.post("/api/coding/workspaces", json={"name": "preview-test"})
    assert r.status_code == 200, r.text
    data = r.json()
    ws_id = data["id"]
    ws_dir = app.state.data_dir / "coding-workspaces" / ws_id
    yield client, ws_id, ws_dir


@pytest.mark.asyncio
async def test_no_index_returns_404(ws):
    client, ws_id, _ws_dir = ws
    r = await client.get(f"/api/coding/workspaces/{ws_id}/preview")
    assert r.status_code == 404
    assert r.json()["error"] == "no_index"


@pytest.mark.asyncio
async def test_inlines_local_css_js_and_image(ws):
    client, ws_id, ws_dir = ws

    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    (ws_dir / "img.png").write_bytes(png_bytes)
    (ws_dir / "style.css").write_text("body { color: red; }\n")
    (ws_dir / "script.js").write_text("console.log('hello');\n")
    (ws_dir / "index.html").write_text(
        "<html><head>"
        '<link rel="stylesheet" href="style.css">'
        "</head><body>"
        '<img src="img.png">'
        '<script src="script.js"></script>'
        "</body></html>"
    )

    r = await client.get(f"/api/coding/workspaces/{ws_id}/preview")
    assert r.status_code == 200, r.text
    html = r.text

    assert "<style>body { color: red; }" in html
    assert "console.log('hello');" in html
    assert "style.css" not in html
    assert "script.js" not in html

    expected_data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"
    assert expected_data_uri in html
    assert "img.png" not in html


@pytest.mark.asyncio
async def test_external_ref_left_untouched(ws):
    client, ws_id, ws_dir = ws
    (ws_dir / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="https://example.com/style.css"></head>'
        "<body></body></html>"
    )

    r = await client.get(f"/api/coding/workspaces/{ws_id}/preview")
    assert r.status_code == 200, r.text
    assert 'href="https://example.com/style.css"' in r.text


@pytest.mark.asyncio
async def test_traversal_ref_not_inlined(ws):
    client, ws_id, ws_dir = ws
    # A secret file outside the workspace's own tree (in its parent dir).
    (ws_dir.parent / "secret.txt").write_text("top secret")
    (ws_dir / "index.html").write_text(
        '<html><head><script src="../../secret.txt"></script></head>'
        "<body></body></html>"
    )

    r = await client.get(f"/api/coding/workspaces/{ws_id}/preview")
    assert r.status_code == 200, r.text
    assert "top secret" not in r.text
    # The unresolved reference is left in place untouched.
    assert 'src="../../secret.txt"' in r.text


@pytest.mark.asyncio
async def test_oversized_asset_not_inlined(ws):
    client, ws_id, ws_dir = ws
    # A stylesheet over the 2 MB per-asset cap must be skipped (left as a
    # <link>), not inlined into the assembled document.
    big_css = "a{color:red}\n" + ("/* pad */\n" * 200_000)
    assert len(big_css.encode("utf-8")) > 2_000_000
    (ws_dir / "big.css").write_text(big_css)
    (ws_dir / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="big.css"></head>'
        "<body></body></html>"
    )

    r = await client.get(f"/api/coding/workspaces/{ws_id}/preview")
    assert r.status_code == 200, r.text
    assert '<link rel="stylesheet" href="big.css">' in r.text
    assert "<style>" not in r.text


@pytest.mark.asyncio
async def test_unknown_workspace_returns_404(ws):
    client, _ws_id, _ws_dir = ws
    r = await client.get("/api/coding/workspaces/cws-notreal/preview")
    assert r.status_code == 404
