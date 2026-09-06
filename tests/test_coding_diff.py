"""Tests for coding workspace diff / accept / revert endpoints."""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def ws(app, client):
    """Create a fresh workspace and return (client, workspace_id, workspace_dir)."""
    store = app.state.coding_workspaces
    if store._db is not None:
        await store.close()
    await store.init()

    r = await client.post("/api/coding/workspaces", json={"name": "diff-test"})
    assert r.status_code == 200, r.text
    data = r.json()
    ws_id = data["id"]
    ws_dir = app.state.data_dir / "coding-workspaces" / ws_id
    yield client, ws_id, ws_dir


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_diff_empty_workspace(ws):
    client, ws_id, _ = ws
    r = await client.get(f"/api/coding/workspaces/{ws_id}/diff")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_diff_shows_untracked_file(ws):
    client, ws_id, ws_dir = ws
    (ws_dir / "hello.py").write_text("print('hello')\n")

    r = await client.get(f"/api/coding/workspaces/{ws_id}/diff")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["path"] == "hello.py"
    assert entry["status"] == "added"
    assert "+print('hello')" in entry["patch"]


@pytest.mark.asyncio
async def test_diff_shows_modified_tracked_file(ws):
    client, ws_id, ws_dir = ws
    # Write + commit initial version
    await client.put(
        f"/api/coding/workspaces/{ws_id}/file",
        json={"path": "app.py", "content": "x = 1\n"},
    )
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "git", "add", ".", cwd=str(ws_dir),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    proc = await asyncio.create_subprocess_exec(
        "git", "-c", "user.email=test@test.com", "-c", "user.name=test",
        "commit", "-m", "initial",
        cwd=str(ws_dir),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()

    # Modify
    (ws_dir / "app.py").write_text("x = 2\n")

    r = await client.get(f"/api/coding/workspaces/{ws_id}/diff")
    assert r.status_code == 200
    entries = {e["path"]: e for e in r.json()}
    assert "app.py" in entries
    assert entries["app.py"]["status"] == "modified"
    patch = entries["app.py"]["patch"]
    assert "-x = 1" in patch
    assert "+x = 2" in patch


@pytest.mark.asyncio
async def test_diff_unknown_workspace_returns_404(ws):
    client, _ws_id, _ = ws
    r = await client.get("/api/coding/workspaces/cws-notreal/diff")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_accept_commits_untracked_file(ws):
    client, ws_id, ws_dir = ws
    import asyncio

    # Need a git identity for the commit
    proc = await asyncio.create_subprocess_exec(
        "git", "-c", "user.email=t@t.com", "-c", "user.name=t",
        "config", "user.email", "t@t.com",
        cwd=str(ws_dir), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    proc = await asyncio.create_subprocess_exec(
        "git", "config", "user.name", "tester",
        cwd=str(ws_dir), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()

    (ws_dir / "main.py").write_text("print('hi')\n")

    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/accept",
        json={"paths": ["main.py"]},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "main.py" in data["committed"]

    # Diff should now be empty (file committed)
    r = await client.get(f"/api/coding/workspaces/{ws_id}/diff")
    paths = [e["path"] for e in r.json()]
    assert "main.py" not in paths


@pytest.mark.asyncio
async def test_accept_invalid_path_rejected(ws):
    client, ws_id, _ = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/accept",
        json={"paths": ["../escape.py"]},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_accept_empty_paths_rejected(ws):
    client, ws_id, _ = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/accept",
        json={"paths": []},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# revert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revert_deletes_untracked_file(ws):
    client, ws_id, ws_dir = ws
    (ws_dir / "temp.py").write_text("# temp\n")
    assert (ws_dir / "temp.py").exists()

    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/revert",
        json={"paths": ["temp.py"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert not (ws_dir / "temp.py").exists()


@pytest.mark.asyncio
async def test_revert_restores_tracked_file(ws):
    client, ws_id, ws_dir = ws
    import asyncio

    await client.put(
        f"/api/coding/workspaces/{ws_id}/file",
        json={"path": "src.py", "content": "original\n"},
    )
    for cmd in [
        ["git", "add", "."],
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "base"],
    ]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ws_dir),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

    # Corrupt the file
    (ws_dir / "src.py").write_text("corrupted\n")
    assert (ws_dir / "src.py").read_text() == "corrupted\n"

    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/revert",
        json={"paths": ["src.py"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert (ws_dir / "src.py").read_text() == "original\n"


@pytest.mark.asyncio
async def test_revert_invalid_path_rejected(ws):
    client, ws_id, _ = ws
    r = await client.post(
        f"/api/coding/workspaces/{ws_id}/revert",
        json={"paths": ["/etc/passwd"]},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_revert_unknown_workspace_returns_404(ws):
    client, _ws_id, _ = ws
    r = await client.post(
        "/api/coding/workspaces/cws-notreal/revert",
        json={"paths": ["x.py"]},
    )
    assert r.status_code == 404
