"""Tests for POST /api/coding/workspaces/{id}/apply-blocks."""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def ws(app, client):
    store = app.state.coding_workspaces
    if store._db is not None:
        await store.close()
    await store.init()

    r = await client.post("/api/coding/workspaces", json={"name": "apply-test"})
    assert r.status_code == 200, r.text
    data = r.json()
    ws_id = data["id"]
    ws_dir = app.state.data_dir / "coding-workspaces" / ws_id
    yield client, ws_id, ws_dir


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_single_block(ws):
    client, ws_id, ws_dir = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={"blocks": [{"path": "src/App.tsx", "content": "export default function App() {}\n"}]},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["applied"] == ["src/App.tsx"]
    assert (ws_dir / "src" / "App.tsx").read_text() == "export default function App() {}\n"


@pytest.mark.asyncio
async def test_apply_multiple_blocks(ws):
    client, ws_id, ws_dir = ws
    blocks = [
        {"path": "index.ts", "content": "console.log('hi');\n"},
        {"path": "lib/util.ts", "content": "export const x = 1;\n"},
    ]
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={"blocks": blocks},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data["applied"]) == {"index.ts", "lib/util.ts"}
    assert (ws_dir / "index.ts").exists()
    assert (ws_dir / "lib" / "util.ts").exists()


@pytest.mark.asyncio
async def test_apply_overwrites_existing_file(ws):
    client, ws_id, ws_dir = ws
    (ws_dir / "main.py").write_text("old content\n")
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={"blocks": [{"path": "main.py", "content": "new content\n"}]},
    )
    assert r.status_code == 200, r.text
    assert (ws_dir / "main.py").read_text() == "new content\n"


@pytest.mark.asyncio
async def test_apply_blocks_shows_in_diff(ws):
    client, ws_id, ws_dir = ws
    await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={"blocks": [{"path": "hello.py", "content": "print('hello')\n"}]},
    )
    r = await client.get(f"/api/coding/workspaces/{ws_id}/diff")
    assert r.status_code == 200
    paths = [e["path"] for e in r.json()]
    assert "hello.py" in paths


# ---------------------------------------------------------------------------
# jail / path validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_blocks_rejects_traversal(ws):
    client, ws_id, _ = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={"blocks": [{"path": "../escape.py", "content": "bad\n"}]},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_apply_blocks_rejects_absolute_path(ws):
    client, ws_id, _ = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={"blocks": [{"path": "/etc/passwd", "content": "bad\n"}]},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_apply_blocks_rejects_empty_list(ws):
    client, ws_id, _ = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={"blocks": []},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_apply_blocks_all_or_nothing_on_bad_path(ws):
    """If any path is invalid the entire request is rejected; no files are written."""
    client, ws_id, ws_dir = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/apply-blocks",
        json={
            "blocks": [
                {"path": "ok.py", "content": "x = 1\n"},
                {"path": "../escape.py", "content": "bad\n"},
            ]
        },
    )
    assert r.status_code == 400
    # The good file must NOT have been written
    assert not (ws_dir / "ok.py").exists()


@pytest.mark.asyncio
async def test_apply_blocks_unknown_workspace(ws):
    client, _ws_id, _ = ws
    r = await client.post(
        "/api/coding/workspaces/cws-notreal/apply-blocks",
        json={"blocks": [{"path": "x.py", "content": "x\n"}]},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# _resolve_jailed helper (unit-level via the route module)
# ---------------------------------------------------------------------------

def test_resolve_jailed_rejects_dotdot(tmp_path):
    from tinyagentos.routes.coding import _resolve_jailed
    assert _resolve_jailed(tmp_path, "../outside.py") is None


def test_resolve_jailed_rejects_absolute(tmp_path):
    from tinyagentos.routes.coding import _resolve_jailed
    assert _resolve_jailed(tmp_path, "/etc/passwd") is None


def test_resolve_jailed_allows_nested(tmp_path):
    from tinyagentos.routes.coding import _resolve_jailed
    result = _resolve_jailed(tmp_path, "src/nested/file.py")
    assert result is not None
    assert result == (tmp_path / "src" / "nested" / "file.py").resolve()


def test_resolve_jailed_rejects_root_without_flag(tmp_path):
    from tinyagentos.routes.coding import _resolve_jailed
    assert _resolve_jailed(tmp_path, "") is None


def test_resolve_jailed_allows_root_with_flag(tmp_path):
    from tinyagentos.routes.coding import _resolve_jailed
    result = _resolve_jailed(tmp_path, "", allow_root=True)
    assert result == tmp_path.resolve()
