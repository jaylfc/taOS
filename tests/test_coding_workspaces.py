import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def coding_client(app, client):
    store = app.state.coding_workspaces
    if store._db is not None:
        await store.close()
    await store.init()
    yield client, app


@pytest.mark.asyncio
async def test_create_list_git_repo(coding_client):
    client, app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "demo"})
    assert r.status_code == 200, r.text
    ws = r.json()
    assert ws["id"]
    assert ws["name"] == "demo"
    assert (app.state.data_dir / "coding-workspaces" / ws["id"]).is_dir()
    assert (app.state.data_dir / "coding-workspaces" / ws["id"] / ".git").exists()

    r = await client.get("/api/coding/workspaces")
    assert r.status_code == 200
    assert any(row["id"] == ws["id"] for row in r.json())


@pytest.mark.asyncio
async def test_write_read_and_tree(coding_client):
    client, _app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "files"})
    ws = r.json()

    r = await client.put(
        f"/api/coding/workspaces/{ws['id']}/file",
        json={"path": "src/hello.txt", "content": "hello world"},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/coding/workspaces/{ws['id']}/file", params={"path": "src/hello.txt"})
    assert r.status_code == 200
    assert r.json()["content"] == "hello world"

    r = await client.get(f"/api/coding/workspaces/{ws['id']}/files", params={"subpath": "src"})
    assert r.status_code == 200
    names = {e["name"]: e["is_dir"] for e in r.json()}
    assert names["hello.txt"] is False


@pytest.mark.asyncio
async def test_path_traversal_rejected(coding_client):
    client, _app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "jail"})
    ws = r.json()
    wid = ws["id"]

    for params in (
        {"path": "../secret"},
        {"path": "/etc/passwd"},
        {"subpath": ".."},
        {"subpath": "/etc"},
    ):
        if "path" in params:
            r = await client.get(f"/api/coding/workspaces/{wid}/file", params=params)
        else:
            r = await client.get(f"/api/coding/workspaces/{wid}/files", params=params)
        assert r.status_code == 400, params

    r = await client.put(
        f"/api/coding/workspaces/{wid}/file",
        json={"path": "../escape.txt", "content": "nope"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_removes_workspace(coding_client):
    client, app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "temp"})
    ws = r.json()
    workspace_dir = app.state.data_dir / "coding-workspaces" / ws["id"]
    assert workspace_dir.is_dir()

    r = await client.delete(f"/api/coding/workspaces/{ws['id']}")
    assert r.status_code == 200

    r = await client.get("/api/coding/workspaces")
    assert all(row["id"] != ws["id"] for row in r.json())
    assert not workspace_dir.exists()


@pytest.mark.asyncio
async def test_list_root_files(coding_client):
    client, _app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "root-list"})
    ws = r.json()

    r = await client.put(
        f"/api/coding/workspaces/{ws['id']}/file",
        json={"path": "readme.txt", "content": "hi"},
    )
    assert r.status_code == 200

    r = await client.get(f"/api/coding/workspaces/{ws['id']}/files")
    assert r.status_code == 200
    names = {e["name"] for e in r.json()}
    assert "readme.txt" in names


@pytest.mark.asyncio
async def test_read_binary_file_rejected(coding_client):
    client, app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "binary"})
    ws = r.json()
    workspace_dir = app.state.data_dir / "coding-workspaces" / ws["id"]
    (workspace_dir / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")

    r = await client.get(
        f"/api/coding/workspaces/{ws['id']}/file",
        params={"path": "blob.bin"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "binary_or_undecodable"


@pytest.mark.asyncio
async def test_list_root_empty_workspace(coding_client):
    client, _app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "fresh-root"})
    ws = r.json()

    r = await client.get(f"/api/coding/workspaces/{ws['id']}/files")
    assert r.status_code == 200
    names = {e["name"] for e in r.json()}
    assert ".git" in names

    r = await client.get(f"/api/coding/workspaces/{ws['id']}/files", params={"subpath": ""})
    assert r.status_code == 200

    r = await client.get(f"/api/coding/workspaces/{ws['id']}/files", params={"subpath": "."})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_read_oversized_file_rejected(coding_client):
    client, app = coding_client
    r = await client.post("/api/coding/workspaces", json={"name": "bigfile"})
    ws = r.json()
    workspace_dir = app.state.data_dir / "coding-workspaces" / ws["id"]
    (workspace_dir / "huge.txt").write_bytes(b"x" * (2_000_001))

    r = await client.get(
        f"/api/coding/workspaces/{ws['id']}/file",
        params={"path": "huge.txt"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "file too large"


@pytest.mark.asyncio
async def test_git_init_failure_no_orphan_dir(coding_client, monkeypatch):
    client, app = coding_client

    async def _fake_git_init(self, workspace_dir):
        raise RuntimeError("git init failed")

    monkeypatch.setattr(
        app.state.coding_workspaces.__class__, "_git_init", _fake_git_init
    )

    r = await client.post("/api/coding/workspaces", json={"name": "broken-git"})
    assert r.status_code == 503

    rows = app.state.coding_workspaces
    listed = await rows.list()
    assert all(row["name"] != "broken-git" for row in listed)

    # The store rmtree's the workspace dir when git init fails, so no orphan dir
    # (named by its generated id, never "broken-git") is left behind.
    workspace_dirs = [
        d for d in (app.state.data_dir / "coding-workspaces").iterdir() if d.is_dir()
    ]
    assert workspace_dirs == []


@pytest.mark.asyncio
async def test_unknown_workspace_returns_404(coding_client):
    client, _app = coding_client
    wid = "cws-nosuch"
    r = await client.get(f"/api/coding/workspaces/{wid}/files")
    assert r.status_code == 404
    r = await client.get(f"/api/coding/workspaces/{wid}/file", params={"path": "a.txt"})
    assert r.status_code == 404
    r = await client.put(
        f"/api/coding/workspaces/{wid}/file",
        json={"path": "a.txt", "content": "x"},
    )
    assert r.status_code == 404
    r = await client.delete(f"/api/coding/workspaces/{wid}")
    assert r.status_code == 404