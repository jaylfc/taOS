"""Tests for the built-in Photos location of the Files app (tsk-cbop43).

The phone camera writes to ``~/Pictures/taOS`` of the account the controller
runs as, which sits outside every root the Files app lists today. Photos is a
built-in location on that folder: listed only when the directory exists, and
served by the same listing, download and delete code, and the same path
traversal guard, as the workspace locations. The tests below assert that parity
against ``/api/workspace/files`` wherever the card asks for it.
"""
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.routes.user_workspace import USER_WORKSPACE_DIR_NAME


def _photos_dir(monkeypatch, tmp_path):
    """Point the built-in location at a temp Pictures directory."""
    base = tmp_path / "Pictures"
    monkeypatch.setenv("TAOS_PHOTOS_DIR", str(base))
    return base


def _member_client(app, username: str = "member") -> AsyncClient:
    """A cookie'd, CSRF-armed client for a non-admin member session on *app*.

    The invite lifecycle is the only way to mint a member (``setup_user`` takes
    the first user only), and the admin created by the ``client`` fixture has to
    exist first, so every caller depends on ``client``. CSRF is armed on purpose:
    a member's DELETE must reach the route's authz check, not the CSRF guard.
    """
    auth_mgr = app.state.auth
    record = auth_mgr.find_user(username)
    if record is None:
        code = auth_mgr.add_user_invite(username, "admin")
        auth_mgr.complete_invite(username, code, "Test Member", "", "memberphotospass1")
        record = auth_mgr.find_user(username)
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


def _local_token_client(app) -> AsyncClient:
    """A client presenting the host local token, i.e. a deployed agent.

    The middleware stamps ``request.state.via == "local_token"`` for this
    credential, which is how the routes tell an agent from a browser session.
    """
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {app.state.auth.get_local_token()}"},
    )


@pytest_asyncio.fixture
async def workspace_root(client, tmp_path):
    """The user workspace root, created through the app's own path.

    The workspace location resolves and creates its root on first request, so a
    single listing initialises it. This keeps the traversal test focused on the
    guard rather than on an incidental upload.
    """
    root = tmp_path / USER_WORKSPACE_DIR_NAME
    resp = await client.get("/api/workspace/files")
    assert resp.status_code == 200
    assert root.is_dir()
    return root


class TestPhotosLocationRoutes:

    @pytest.mark.asyncio
    async def test_location_absent_when_directory_missing(self, client, monkeypatch, tmp_path):
        """No Pictures folder means no Photos entry."""
        _photos_dir(monkeypatch, tmp_path)
        resp = await client.get("/api/files/locations")
        assert resp.status_code == 200
        assert [entry["id"] for entry in resp.json()] == []

    @pytest.mark.asyncio
    async def test_location_listed_when_directory_exists(self, client, monkeypatch, tmp_path):
        """An existing Pictures folder is listed as the Photos location."""
        _photos_dir(monkeypatch, tmp_path).mkdir()
        resp = await client.get("/api/files/locations")
        assert resp.status_code == 200
        assert {"id": "photos", "label": "Photos"} in resp.json()

    @pytest.mark.asyncio
    async def test_lists_files_in_the_directory(self, client, monkeypatch, tmp_path):
        """Listing returns the folder's contents in the workspace listing shape."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        (base / "IMG_0001.jpg").write_bytes(b"jpeg-bytes")
        (base / "shots").mkdir()

        resp = await client.get("/api/photos/files")
        assert resp.status_code == 200
        entries = resp.json()
        by_name = {e["name"]: e for e in entries}
        assert set(by_name) == {"IMG_0001.jpg", "shots"}
        assert by_name["shots"]["is_dir"] is True
        assert by_name["IMG_0001.jpg"]["path"] == "IMG_0001.jpg"
        assert by_name["IMG_0001.jpg"]["size"] == len(b"jpeg-bytes")

    @pytest.mark.asyncio
    async def test_download_and_thumbnail_path_serves_the_file(self, client, monkeypatch, tmp_path):
        """GET /api/photos/files/<path> streams the file (download and thumbnail)."""
        base = _photos_dir(monkeypatch, tmp_path)
        (base / "sub").mkdir(parents=True)
        (base / "sub" / "IMG_0002.jpg").write_bytes(b"thumbnail-bytes")

        resp = await client.get("/api/photos/files/sub/IMG_0002.jpg")
        assert resp.status_code == 200
        assert resp.content == b"thumbnail-bytes"

    @pytest.mark.asyncio
    async def test_delete_removes_the_file(self, client, monkeypatch, tmp_path):
        """Deleting a photo removes it from the folder, as the other locations do."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        target = base / "IMG_0003.jpg"
        target.write_bytes(b"bye")

        resp = await client.delete("/api/photos/files/IMG_0003.jpg")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert not target.exists()
        listing = await client.get("/api/photos/files")
        assert [e["name"] for e in listing.json()] == []

    @pytest.mark.asyncio
    async def test_delete_missing_file_returns_404(self, client, monkeypatch, tmp_path):
        _photos_dir(monkeypatch, tmp_path).mkdir()
        resp = await client.delete("/api/photos/files/ghost.jpg")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_traversal_refused_as_in_the_workspace_location(
        self, client, workspace_root, monkeypatch, tmp_path
    ):
        """``..``, an absolute path and a symlink out of the base are refused with
        the SAME status the user workspace location refuses them with."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"outside")
        # A symlink that resolves outside the base, in both locations.
        (base / "escape.txt").symlink_to(outside)
        (workspace_root / "escape.txt").symlink_to(outside)

        spellings = [
            ("dotdot_query", lambda loc: f"/api/{loc}/files?path=../../etc"),
            ("dotdot_path", lambda loc: f"/api/{loc}/files/%2e%2e/etc/hostname"),
            ("absolute", lambda loc: f"/api/{loc}/files/%2Fetc%2Fhostname"),
            ("symlink_out", lambda loc: f"/api/{loc}/files/escape.txt"),
        ]
        for name, build in spellings:
            workspace_status = (await client.get(build("workspace"))).status_code
            resp = await client.get(build("photos"))
            assert resp.status_code == 400, f"{name}: {resp.status_code}"
            assert resp.status_code == workspace_status, name

    @pytest.mark.asyncio
    async def test_delete_refuses_the_base_folder_itself(self, client, monkeypatch, tmp_path):
        """Deleting the location itself must not move the whole Pictures folder.

        Every one of these spellings resolves to the base directory after the
        traversal guard, so without a guard they would trash the folder and
        everything in it, which is not what "delete" means here. The dots are
        percent-encoded because an HTTP client removes a bare "." / ".." segment
        from the URL before it is ever sent; encoded, they reach the route.
        """
        base = _photos_dir(monkeypatch, tmp_path)
        (base / "sub").mkdir(parents=True)
        photo = base / "IMG_0004.jpg"
        photo.write_bytes(b"keep me")

        for spelling in ("/api/photos/files/", "/api/photos/files/%2e",
                         "/api/photos/files/%2E", "/api/photos/files/sub%2F..",
                         "/api/photos/files/sub/%2e%2e"):
            resp = await client.delete(spelling)
            assert resp.status_code == 400, spelling
            assert resp.json() == {"error": "Invalid path"}, spelling

        assert base.is_dir(), "the Pictures folder itself must still be there"
        assert photo.read_bytes() == b"keep me"
        assert (base / "sub").is_dir()

    @pytest.mark.asyncio
    async def test_delete_through_symlinks_outside_is_refused(self, client, monkeypatch, tmp_path):
        """A delete follows no symlink out of the base, file or directory."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        outside_file = tmp_path / "outside" / "keep.jpg"
        outside_file.parent.mkdir()
        outside_file.write_bytes(b"keep")
        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "inner.jpg").write_bytes(b"inner")
        (base / "link.jpg").symlink_to(outside_file)
        (base / "link-dir").symlink_to(outside_dir, target_is_directory=True)

        for name in ("link.jpg", "link-dir"):
            resp = await client.delete(f"/api/photos/files/{name}")
            assert resp.status_code == 400, name
            assert resp.json() == {"error": "Invalid path"}, name

        assert outside_file.read_bytes() == b"keep"
        assert (outside_dir / "inner.jpg").read_bytes() == b"inner"
        assert (base / "link.jpg").is_symlink()
        assert (base / "link-dir").is_symlink()

    @pytest.mark.asyncio
    async def test_delete_in_folder_symlink_trashes_the_link_not_its_target(
        self, client, monkeypatch, tmp_path
    ):
        """An alias photo is deleted as the alias, never as its target.

        ``alias.jpg -> real.jpg`` inside the folder is one entry in the user's
        listing. The delete resolved the final path component, so it moved
        ``real.jpg`` into the recycle bin under the name ``alias.jpg`` and left
        the alias behind: the real photo was gone and the link dangled. The
        parent is resolved, the entry itself never is; the link moves, its
        target stays, and restore puts the link back.
        """
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        target = base / "real.jpg"
        target.write_bytes(b"the real photo")
        (base / "alias.jpg").symlink_to("real.jpg")

        resp = await client.delete("/api/photos/files/alias.jpg")
        assert resp.status_code == 200
        assert not (base / "alias.jpg").is_symlink()
        assert not (base / "alias.jpg").exists()
        assert target.read_bytes() == b"the real photo"

        items = (await client.get("/api/photos/trash")).json()["items"]
        assert [item["original_path"] for item in items] == ["alias.jpg"]
        assert [item["name"] for item in items] == ["alias.jpg"]

        restored = await client.post(f"/api/photos/trash/{items[0]['id']}/restore")
        assert restored.status_code == 200
        assert (base / "alias.jpg").is_symlink()
        assert (base / "alias.jpg").resolve() == target
        assert (base / "alias.jpg").read_bytes() == b"the real photo"
        assert target.exists()

    @pytest.mark.asyncio
    async def test_symlinked_pictures_folder_lists_its_subfolders(self, client, monkeypatch, tmp_path):
        """A Pictures folder that is itself a symlink still works as a location.

        A camera roll moved to another disk leaves ``~/Pictures`` as a link to
        the new place. Listing a subfolder through that link made the shared
        listing compute ``relative_to`` an unresolved base, a ValueError that
        answered 500. The route resolves the base, so items inside the folder
        stay inside it.
        """
        realpics = tmp_path / "realpics"
        (realpics / "sub").mkdir(parents=True)
        (realpics / "sub" / "IMG_0009.jpg").write_bytes(b"real bytes")
        pictures = tmp_path / "Pictures"
        pictures.symlink_to(realpics, target_is_directory=True)
        monkeypatch.setenv("TAOS_PHOTOS_DIR", str(pictures))

        resp = await client.get("/api/photos/files?path=sub")
        assert resp.status_code == 200, resp.text
        assert [e["name"] for e in resp.json()] == ["IMG_0009.jpg"]

    @pytest.mark.asyncio
    async def test_dangling_symlink_does_not_break_the_listing(self, client, monkeypatch, tmp_path):
        """A symlink whose target is gone is skipped, not a 500 for the folder.

        The shared listing stats every entry, so one broken symlink (the photo
        was moved off the phone, its link stayed) would take the whole folder
        down with it.
        """
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        (base / "IMG_0005.jpg").write_bytes(b"jpeg-bytes")
        (base / "shots").mkdir()
        (base / "gone.jpg").symlink_to(tmp_path / "no-longer-here.jpg")

        resp = await client.get("/api/photos/files")
        assert resp.status_code == 200
        assert sorted(e["name"] for e in resp.json()) == ["IMG_0005.jpg", "shots"]

    @pytest.mark.asyncio
    async def test_delete_then_restore_from_the_recycle_bin(self, client, app, monkeypatch, tmp_path):
        """A deleted photo is listed by the photos trash and restores in place.

        This is the same move-to-trash scope as the workspace locations, so the
        recycle bin's list and restore routes put it back under the Pictures
        folder it came from, subdirectory included.
        """
        base = _photos_dir(monkeypatch, tmp_path)
        (base / "shots").mkdir(parents=True)
        photo = base / "shots" / "IMG_0006.jpg"
        photo.write_bytes(b"restore me")

        resp = await client.delete("/api/photos/files/shots/IMG_0006.jpg")
        assert resp.status_code == 200
        assert not photo.exists()

        listing = await client.get("/api/photos/trash")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert [item["original_path"] for item in items] == ["shots/IMG_0006.jpg"]

        restored = await client.post(f"/api/photos/trash/{items[0]['id']}/restore")
        assert restored.status_code == 200
        assert photo.read_bytes() == b"restore me"

        assert (await client.get("/api/photos/trash")).json()["items"] == []

    @pytest.mark.asyncio
    async def test_restore_with_escaping_original_path_is_refused(
        self, client, app, monkeypatch, tmp_path
    ):
        """A trash entry whose original_path leaves the folder cannot restore.

        The recorded path only escapes the base through a corrupt or
        hand-edited trash entry, but a restore must refuse with 400 rather
        than write the file outside the Photos folder.
        """
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        trash_dir = app.state.config_path.parent / ".taos-trash" / "photos"
        trash_dir.mkdir(parents=True, exist_ok=True)
        item_id = "0123456789abcdef0123456789abcdef"
        holder = trash_dir / item_id
        holder.mkdir()
        (holder / "ghost.jpg").write_bytes(b"ghost")
        (trash_dir / f"{item_id}.json").write_text(json.dumps({
            "id": item_id,
            "name": "ghost.jpg",
            "original_path": "../escaped.jpg",
            "is_dir": False,
            "size_bytes": 5,
            "deleted_at": "2026-11-19T00:00:00+00:00",
        }))

        resp = await client.post(f"/api/photos/trash/{item_id}/restore")
        assert resp.status_code == 400, resp.text
        assert not (tmp_path / "escaped.jpg").exists()
        assert (holder / "ghost.jpg").read_bytes() == b"ghost"
        assert base.is_dir()

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_refused(self, client, app, monkeypatch, tmp_path):
        """Same authorization as the other locations: a session is required."""
        _photos_dir(monkeypatch, tmp_path).mkdir()
        anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        try:
            for url in ("/api/files/locations", "/api/photos/files"):
                resp = await anon.get(url)
                assert resp.status_code == 401, url
            resp = await anon.delete("/api/photos/files/IMG_0001.jpg")
            assert resp.status_code == 401
        finally:
            await anon.aclose()


class TestPhotosLocationAccess:
    """Who may reach the Pictures folder (owner rulings, tsk-j3gnef).

    Until photo folders are per-user, the location belongs to the machine owner:
    their own session and admins reach it, every other account does not, and an
    agent (a local-token caller) may look but never delete.
    """

    @pytest.mark.asyncio
    async def test_non_owner_member_gets_neither_the_location_nor_the_routes(
        self, client, app, monkeypatch, tmp_path
    ):
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        photo = base / "IMG_0007.jpg"
        photo.write_bytes(b"someone else's camera roll")

        # The admin created by the shared fixture is the primary user: it sees
        # the location, so the member's result below is a difference in caller.
        assert {"id": "photos", "label": "Photos"} in (
            await client.get("/api/files/locations")
        ).json()

        member = _member_client(app)
        try:
            listing = await member.get("/api/files/locations")
            assert listing.status_code == 200
            assert listing.json() == []

            for method, url in (
                (member.get, "/api/photos/files"),
                (member.get, "/api/photos/files/IMG_0007.jpg"),
                (member.get, "/api/photos/trash"),
                (member.delete, "/api/photos/files/IMG_0007.jpg"),
            ):
                resp = await method(url)
                assert resp.status_code == 403, url
            assert photo.exists()
        finally:
            await member.aclose()

    @pytest.mark.asyncio
    async def test_agent_local_token_may_read_but_never_write(self, client, app, monkeypatch, tmp_path):
        """A deployed agent lists and downloads, and is refused on delete."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        photo = base / "IMG_0008.jpg"
        photo.write_bytes(b"jpeg-bytes")

        async with _local_token_client(app) as agent:
            listing = await agent.get("/api/photos/files")
            assert listing.status_code == 200
            assert [e["name"] for e in listing.json()] == ["IMG_0008.jpg"]

            download = await agent.get("/api/photos/files/IMG_0008.jpg")
            assert download.status_code == 200
            assert download.content == b"jpeg-bytes"

            resp = await agent.delete("/api/photos/files/IMG_0008.jpg")
            assert resp.status_code == 403, resp.text
            assert photo.read_bytes() == b"jpeg-bytes"

            assert (await agent.get("/api/photos/trash")).status_code == 200

            restore = await agent.post("/api/photos/trash/deadbeefdeadbeefdeadbeefdeadbeef/restore")
            assert restore.status_code == 403, restore.text
