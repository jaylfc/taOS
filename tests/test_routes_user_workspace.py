"""Tests for the user workspace file browser routes."""
import io
import pytest


class TestUserWorkspaceRoutes:

    @pytest.mark.asyncio
    async def test_list_files_empty(self, client):
        """Listing files in an empty workspace returns an empty list."""
        resp = await client.get("/api/workspace/files")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_upload_file(self, client):
        """Uploading a file returns status uploaded and the file name."""
        content = b"hello workspace"
        resp = await client.post(
            "/api/workspace/files/upload",
            files={"file": ("hello.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "hello.txt"
        assert data["size"] == len(content)
        assert data["status"] == "uploaded"

    @pytest.mark.asyncio
    async def test_upload_and_list(self, client):
        """Uploaded file appears in file listing."""
        content = b"list me"
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("list_me.txt", io.BytesIO(content), "text/plain")},
        )
        resp = await client.get("/api/workspace/files")
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()]
        assert "list_me.txt" in names

    @pytest.mark.asyncio
    async def test_create_directory(self, client):
        """POST /api/workspace/mkdir creates a directory."""
        resp = await client.post("/api/workspace/mkdir", json={"path": "mydir"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "mydir" in data["path"]

    @pytest.mark.asyncio
    async def test_list_subdirectory(self, client):
        """Files uploaded into a subdirectory appear when listing that subdir."""
        # Create subdir
        await client.post("/api/workspace/mkdir", json={"path": "subdir"})
        # Upload into subdir
        content = b"subdir file"
        await client.post(
            "/api/workspace/files/upload?path=subdir",
            files={"file": ("sub.txt", io.BytesIO(content), "text/plain")},
        )
        # List subdir
        resp = await client.get("/api/workspace/files?path=subdir")
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()]
        assert "sub.txt" in names

    @pytest.mark.asyncio
    async def test_delete_file(self, client):
        """Deleting an uploaded file returns status deleted and removes it from listing."""
        content = b"delete me"
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("to_delete.txt", io.BytesIO(content), "text/plain")},
        )
        # Verify it exists
        list_resp = await client.get("/api/workspace/files")
        names = [e["name"] for e in list_resp.json()]
        assert "to_delete.txt" in names

        # Delete it
        del_resp = await client.delete("/api/workspace/files/to_delete.txt")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "deleted"

        # Verify gone
        list_resp2 = await client.get("/api/workspace/files")
        names2 = [e["name"] for e in list_resp2.json()]
        assert "to_delete.txt" not in names2

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client):
        """Deleting a file that does not exist returns 404."""
        resp = await client.delete("/api/workspace/files/ghost_file.txt")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, client):
        """Path traversal attempts are blocked with 400."""
        resp = await client.get("/api/workspace/files?path=../../etc")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rename_file(self, client):
        """POST /api/workspace/rename moves a file to its new name."""
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("old.txt", io.BytesIO(b"x"), "text/plain")},
        )
        resp = await client.post("/api/workspace/rename", json={"src": "old.txt", "dst": "new.txt"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "renamed"
        names = [e["name"] for e in (await client.get("/api/workspace/files")).json()]
        assert "new.txt" in names and "old.txt" not in names

    @pytest.mark.asyncio
    async def test_rename_nonexistent_returns_404(self, client):
        """Renaming a missing source returns 404."""
        resp = await client.post("/api/workspace/rename", json={"src": "ghost", "dst": "x"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rename_onto_existing_returns_409(self, client):
        """Renaming onto an existing target returns 409."""
        for n in ("a.txt", "b.txt"):
            await client.post(
                "/api/workspace/files/upload",
                files={"file": (n, io.BytesIO(b"x"), "text/plain")},
            )
        resp = await client.post("/api/workspace/rename", json={"src": "a.txt", "dst": "b.txt"})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_rename_traversal_blocked(self, client):
        """Rename targets outside the workspace are blocked with 400."""
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("safe.txt", io.BytesIO(b"x"), "text/plain")},
        )
        resp = await client.post("/api/workspace/rename", json={"src": "safe.txt", "dst": "../escape.txt"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_copy_file(self, client):
        """POST /api/workspace/copy duplicates a file, leaving the source intact."""
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("orig.txt", io.BytesIO(b"copy me"), "text/plain")},
        )
        resp = await client.post("/api/workspace/copy", json={"src": "orig.txt", "dst": "dup.txt"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "copied"
        names = [e["name"] for e in (await client.get("/api/workspace/files")).json()]
        assert "dup.txt" in names and "orig.txt" in names

    @pytest.mark.asyncio
    async def test_copy_in_place_with_copy_suffix(self, client):
        """Duplicating a file in the same directory under a "copy" name works.

        The Files app picks a non-colliding "<base> copy<ext>" destination when
        pasting into the source directory; the backend must accept that dst.
        """
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("note.txt", io.BytesIO(b"dup in place"), "text/plain")},
        )
        resp = await client.post(
            "/api/workspace/copy", json={"src": "note.txt", "dst": "note copy.txt"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "copied"
        names = [e["name"] for e in (await client.get("/api/workspace/files")).json()]
        assert "note.txt" in names and "note copy.txt" in names

    @pytest.mark.asyncio
    async def test_copy_directory_tree(self, client):
        """Copying a directory copies its contents recursively."""
        await client.post("/api/workspace/mkdir", json={"path": "srcdir"})
        await client.post(
            "/api/workspace/files/upload?path=srcdir",
            files={"file": ("inner.txt", io.BytesIO(b"nested"), "text/plain")},
        )
        resp = await client.post("/api/workspace/copy", json={"src": "srcdir", "dst": "dstdir"})
        assert resp.status_code == 200
        names = [e["name"] for e in (await client.get("/api/workspace/files?path=dstdir")).json()]
        assert "inner.txt" in names

    @pytest.mark.asyncio
    async def test_copy_onto_existing_returns_409(self, client):
        """Copying onto an existing target returns 409."""
        for n in ("c.txt", "d.txt"):
            await client.post(
                "/api/workspace/files/upload",
                files={"file": (n, io.BytesIO(b"x"), "text/plain")},
            )
        resp = await client.post("/api/workspace/copy", json={"src": "c.txt", "dst": "d.txt"})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_copy_nonexistent_returns_404(self, client):
        """Copying a missing source returns 404."""
        resp = await client.post("/api/workspace/copy", json={"src": "ghost", "dst": "x"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_copy_traversal_blocked(self, client):
        """Copy sources/targets outside the workspace are blocked with 400."""
        resp = await client.post("/api/workspace/copy", json={"src": "../../etc", "dst": "stolen.txt"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_copy_directory_into_itself_returns_400(self, client):
        """Copying a directory into a path nested inside itself returns a clean
        400, not a 500 from shutil.copytree."""
        await client.post("/api/workspace/mkdir", json={"path": "selfdir"})
        resp = await client.post(
            "/api/workspace/copy", json={"src": "selfdir", "dst": "selfdir/nested"}
        )
        assert resp.status_code == 400
        assert "itself" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_move_directory_into_itself_returns_400(self, client):
        """Renaming/moving a directory into a path nested inside itself returns a
        clean 400, not a 500."""
        await client.post("/api/workspace/mkdir", json={"path": "movedir"})
        resp = await client.post(
            "/api/workspace/rename", json={"src": "movedir", "dst": "movedir/inner"}
        )
        assert resp.status_code == 400
        assert "itself" in resp.json()["error"].lower()

    def test_dir_signature_changes_on_modification(self):
        """Signature helper drives the SSE watch change-detection loop.
        Unit-tested directly — the full SSE stream endpoint is tested
        via the frontend EventSource path in production (ASGI test
        clients don't handle infinite-body streams cleanly)."""
        from tinyagentos.routes.user_workspace import _dir_signature
        assert _dir_signature([]) == ""
        a = [{"name": "a.txt", "modified": 100.0, "size": 5}]
        b = [{"name": "a.txt", "modified": 200.0, "size": 5}]
        c = [{"name": "a.txt", "modified": 100.0, "size": 10}]
        d = [{"name": "a.txt", "modified": 100.0, "size": 5}, {"name": "b.txt", "modified": 100.0, "size": 0}]
        assert _dir_signature(a) != _dir_signature(b)  # mtime change
        assert _dir_signature(a) != _dir_signature(c)  # size change
        assert _dir_signature(a) != _dir_signature(d)  # new file
        assert _dir_signature(a) == _dir_signature(a)  # stable

    def test_list_dir_helper_roundtrip(self, tmp_path):
        """_list_dir returns the shape the watch endpoint streams."""
        from tinyagentos.routes.user_workspace import _list_dir
        (tmp_path / "a.txt").write_bytes(b"hello")
        (tmp_path / "sub").mkdir()
        entries = _list_dir(tmp_path, "")
        assert isinstance(entries, list)
        names = {e["name"] for e in entries}
        assert names == {"a.txt", "sub"}
        # Ordering: dirs before files, alphabetical within each group
        assert entries[0]["name"] == "sub"
        assert entries[1]["name"] == "a.txt"

    def test_list_dir_rejects_traversal(self, tmp_path):
        """Attempting to escape the workspace root returns an error tuple."""
        from tinyagentos.routes.user_workspace import _list_dir
        result = _list_dir(tmp_path, "../../etc")
        assert isinstance(result, tuple)
        assert result[0] == 400
        assert "error" in result[1]

    @pytest.mark.asyncio
    async def test_storage_stats(self, client):
        """GET /api/workspace/stats returns total_files and total_size."""
        # Upload a file so stats are non-trivial
        content = b"stats check"
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("stats.txt", io.BytesIO(content), "text/plain")},
        )
        resp = await client.get("/api/workspace/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_files" in data
        assert "total_size" in data
        assert data["total_files"] >= 1
        assert data["total_size"] >= len(content)
