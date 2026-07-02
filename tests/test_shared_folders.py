from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.shared_folders import SharedFolderManager


@pytest_asyncio.fixture
async def folder_mgr(tmp_path):
    storage_dir = tmp_path / "shared-folders"
    mgr = SharedFolderManager(tmp_path / "shared_folders.db", storage_dir)
    await mgr.init()
    yield mgr
    await mgr.close()


@pytest.mark.asyncio
class TestSharedFolderManager:
    async def test_create_folder(self, folder_mgr):
        fid = await folder_mgr.create_folder("docs", description="Shared docs")
        assert fid is not None
        assert (folder_mgr.storage_dir / "docs").is_dir()

    async def test_list_folders_empty(self, folder_mgr):
        folders = await folder_mgr.list_folders()
        assert folders == []

    async def test_list_folders(self, folder_mgr):
        await folder_mgr.create_folder("alpha")
        await folder_mgr.create_folder("beta")
        folders = await folder_mgr.list_folders()
        assert len(folders) == 2
        assert folders[0]["name"] == "alpha"
        assert folders[1]["name"] == "beta"

    async def test_create_folder_with_agents(self, folder_mgr):
        fid = await folder_mgr.create_folder("team", agents=["naira", "kira"])
        folders = await folder_mgr.list_folders(agent_name="naira")
        assert len(folders) == 1
        assert folders[0]["name"] == "team"
        assert folders[0]["permission"] == "readwrite"

    async def test_list_folders_by_agent(self, folder_mgr):
        fid1 = await folder_mgr.create_folder("team-a", agents=["naira"])
        fid2 = await folder_mgr.create_folder("team-b", agents=["kira"])
        assert len(await folder_mgr.list_folders(agent_name="naira")) == 1
        assert len(await folder_mgr.list_folders(agent_name="kira")) == 1
        assert len(await folder_mgr.list_folders(agent_name="zara")) == 0

    async def test_delete_folder(self, folder_mgr):
        fid = await folder_mgr.create_folder("temp")
        assert (folder_mgr.storage_dir / "temp").is_dir()
        deleted = await folder_mgr.delete_folder(fid)
        assert deleted is True
        assert not (folder_mgr.storage_dir / "temp").exists()
        folders = await folder_mgr.list_folders()
        assert len(folders) == 0

    async def test_delete_nonexistent_folder(self, folder_mgr):
        deleted = await folder_mgr.delete_folder(999)
        assert deleted is False

    async def test_list_files_empty(self, folder_mgr):
        await folder_mgr.create_folder("empty")
        files = folder_mgr.list_files("empty")
        assert files == []

    async def test_list_files(self, folder_mgr):
        await folder_mgr.create_folder("data")
        (folder_mgr.storage_dir / "data" / "test.txt").write_text("hello")
        files = folder_mgr.list_files("data")
        assert len(files) == 1
        assert files[0]["name"] == "test.txt"
        assert files[0]["size_mb"] >= 0

    async def test_list_files_nonexistent_folder(self, folder_mgr):
        files = folder_mgr.list_files("nonexistent")
        assert files == []

    async def test_grant_access(self, folder_mgr):
        fid = await folder_mgr.create_folder("secret")
        await folder_mgr.grant_access(fid, "naira", "read")
        folders = await folder_mgr.list_folders(agent_name="naira")
        assert len(folders) == 1
        assert folders[0]["permission"] == "read"

    async def test_revoke_access(self, folder_mgr):
        fid = await folder_mgr.create_folder("secret", agents=["naira"])
        assert len(await folder_mgr.list_folders(agent_name="naira")) == 1
        await folder_mgr.revoke_access(fid, "naira")
        assert len(await folder_mgr.list_folders(agent_name="naira")) == 0

    async def test_grant_replaces_permission(self, folder_mgr):
        fid = await folder_mgr.create_folder("docs", agents=["naira"])
        await folder_mgr.grant_access(fid, "naira", "read")
        folders = await folder_mgr.list_folders(agent_name="naira")
        assert folders[0]["permission"] == "read"

    async def test_unique_name_constraint(self, folder_mgr):
        await folder_mgr.create_folder("unique")
        with pytest.raises(Exception):
            await folder_mgr.create_folder("unique")


# --- Route tests ---

@pytest.mark.asyncio
async def test_api_list_shared_folders_empty(client):
    resp = await client.get("/api/shared-folders")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_api_create_shared_folder(client):
    resp = await client.post("/api/shared-folders", json={
        "name": "test-folder",
        "description": "A test folder",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert "id" in data


@pytest.mark.asyncio
async def test_api_create_and_list_shared_folders(client):
    await client.post("/api/shared-folders", json={"name": "folder-a"})
    await client.post("/api/shared-folders", json={"name": "folder-b"})
    resp = await client.get("/api/shared-folders")
    assert resp.status_code == 200
    folders = resp.json()
    assert len(folders) == 2


@pytest.mark.asyncio
async def test_api_delete_shared_folder(client):
    resp = await client.post("/api/shared-folders", json={"name": "to-delete"})
    fid = resp.json()["id"]
    resp = await client.delete(f"/api/shared-folders/{fid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_api_delete_nonexistent_folder(client):
    resp = await client.delete("/api/shared-folders/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_list_folder_files(client):
    await client.post("/api/shared-folders", json={"name": "file-test"})
    resp = await client.get("/api/shared-folders/file-test/files")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_api_upload_to_folder(client):
    await client.post("/api/shared-folders", json={"name": "upload-test"})
    resp = await client.post(
        "/api/shared-folders/upload-test/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "uploaded"
    assert data["name"] == "hello.txt"

    # Verify file appears in listing
    resp = await client.get("/api/shared-folders/upload-test/files")
    files = resp.json()
    assert len(files) == 1
    assert files[0]["name"] == "hello.txt"


@pytest.mark.asyncio
async def test_api_upload_to_nonexistent_folder(client):
    resp = await client.post(
        "/api/shared-folders/nonexistent/upload",
        files={"file": ("test.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_grant_access(client):
    resp = await client.post("/api/shared-folders", json={
        "name": "access-test",
    })
    fid = resp.json()["id"]
    resp = await client.post(f"/api/shared-folders/{fid}/access", json={
        "agent_name": "test-agent",
        "permission": "read",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "granted"

    # Verify via filtered listing
    resp = await client.get("/api/shared-folders?agent_name=test-agent")
    folders = resp.json()
    assert len(folders) == 1
    assert folders[0]["permission"] == "read"


@pytest.mark.asyncio
async def test_api_duplicate_folder_returns_409(client):
    await client.post("/api/shared-folders", json={"name": "dup"})
    resp = await client.post("/api/shared-folders", json={"name": "dup"})
    assert resp.status_code == 409


@pytest.mark.asyncio
class TestSharedFolderTraversal:
    """A folder name or filename must never escape storage_dir (path traversal)."""

    async def test_create_folder_rejects_pure_traversal_name(self, folder_mgr):
        # Names with no real basename (bare traversal) are rejected outright.
        for bad in ("..", ".", ""):
            with pytest.raises(ValueError):
                await folder_mgr.create_folder(bad)

    async def test_create_folder_contains_slashy_traversal(self, folder_mgr):
        # A traversal-looking name that has a basename is flattened to it and
        # stays inside the storage root; nothing escapes.
        await folder_mgr.create_folder("../../etc/evil")
        assert (folder_mgr.storage_dir / "evil").is_dir()
        assert not (folder_mgr.storage_dir.parent / "evil").exists()
        # every escape variant normalizes to the same contained basename
        for variant in ("../evil", "/etc/evil", "..\\evil"):
            assert folder_mgr._safe_label(variant) == "evil"

    async def test_create_folder_flattens_slashy_name(self, folder_mgr):
        # A nested-looking name collapses to its basename, staying contained.
        await folder_mgr.create_folder("a/b/docs")
        assert (folder_mgr.storage_dir / "docs").is_dir()
        assert not (folder_mgr.storage_dir / "a").exists()

    async def test_safe_label_blocks_escape(self, folder_mgr):
        assert folder_mgr._safe_label("../../etc/passwd") == "passwd"
        with pytest.raises(ValueError):
            folder_mgr._safe_label("..")
        with pytest.raises(ValueError):
            folder_mgr._safe_label("")
