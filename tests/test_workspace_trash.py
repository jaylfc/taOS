"""Tests for the shared move-to-trash helper (tinyagentos/workspace_trash.py)
and its wiring into the three host-side Files backends: user workspace,
agent workspace, and project files.

Regression coverage for #1604 — Files used to hard-delete
(shutil.rmtree / Path.unlink) on every one of these routes, bypassing the
Recycle Bin entirely. These tests assert the deleted bytes still exist on
disk (moved under a trash directory) and can be listed/restored/purged,
rather than merely asserting the API response shape.
"""
from __future__ import annotations

import io

import pytest

from tinyagentos.workspace_trash import (
    TrashItemNotFound,
    TrashRestoreConflict,
    empty_trash,
    get_trash_dir,
    list_trash_items,
    move_to_trash,
    purge_trash_item,
    restore_trash_item,
)


class TestWorkspaceTrashHelper:
    """Unit tests against a bare tmp_path filesystem — no app/client needed."""

    def test_move_to_trash_relocates_file_not_deletes(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "note.txt"
        target.write_bytes(b"keep me safe")

        trash_dir = get_trash_dir(tmp_path, "workspace")
        metadata = move_to_trash(trash_dir, target, "note.txt")

        # Not hard-deleted: the bytes still exist somewhere on disk.
        assert not target.exists()
        holder = trash_dir / metadata["id"] / "note.txt"
        assert holder.exists()
        assert holder.read_bytes() == b"keep me safe"
        assert metadata["original_path"] == "note.txt"
        assert metadata["is_dir"] is False
        assert metadata["size_bytes"] == len(b"keep me safe")
        assert metadata["deleted_at"]

    def test_move_to_trash_relocates_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "folder"
        target.mkdir()
        (target / "inner.txt").write_bytes(b"nested")

        trash_dir = get_trash_dir(tmp_path, "workspace")
        metadata = move_to_trash(trash_dir, target, "folder")

        assert not target.exists()
        assert metadata["is_dir"] is True
        holder = trash_dir / metadata["id"] / "folder" / "inner.txt"
        assert holder.read_bytes() == b"nested"

    def test_list_trash_items_newest_first(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        trash_dir = get_trash_dir(tmp_path, "workspace")

        for name in ("a.txt", "b.txt"):
            f = workspace / name
            f.write_bytes(b"x")
            move_to_trash(trash_dir, f, name)

        items = list_trash_items(trash_dir)
        assert len(items) == 2
        assert {i["original_path"] for i in items} == {"a.txt", "b.txt"}

    def test_restore_trash_item_roundtrip(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "restore_me.txt"
        target.write_bytes(b"bring it back")
        trash_dir = get_trash_dir(tmp_path, "workspace")
        metadata = move_to_trash(trash_dir, target, "restore_me.txt")

        restored = restore_trash_item(trash_dir, workspace, metadata["id"])
        assert restored["original_path"] == "restore_me.txt"
        assert target.exists()
        assert target.read_bytes() == b"bring it back"
        # Metadata and holder are cleaned up after restore.
        assert list_trash_items(trash_dir) == []

    def test_restore_unknown_id_raises_not_found(self, tmp_path):
        trash_dir = get_trash_dir(tmp_path, "workspace")
        with pytest.raises(TrashItemNotFound):
            restore_trash_item(trash_dir, tmp_path, "does-not-exist")

    def test_restore_conflict_when_destination_exists(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "conflict.txt"
        target.write_bytes(b"original")
        trash_dir = get_trash_dir(tmp_path, "workspace")
        metadata = move_to_trash(trash_dir, target, "conflict.txt")

        # Something new now occupies the original path.
        (workspace / "conflict.txt").write_bytes(b"new file")

        with pytest.raises(TrashRestoreConflict):
            restore_trash_item(trash_dir, workspace, metadata["id"])

    def test_purge_trash_item_permanently_deletes(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "purge_me.txt"
        target.write_bytes(b"gone for good")
        trash_dir = get_trash_dir(tmp_path, "workspace")
        metadata = move_to_trash(trash_dir, target, "purge_me.txt")

        assert purge_trash_item(trash_dir, metadata["id"]) is True
        assert list_trash_items(trash_dir) == []
        assert not (trash_dir / metadata["id"]).exists()

    def test_purge_unknown_item_returns_false(self, tmp_path):
        # A well-formed id that simply doesn't exist returns False; malformed
        # ids raise TrashItemNotFound (covered separately).
        trash_dir = get_trash_dir(tmp_path, "workspace")
        assert purge_trash_item(trash_dir, "0" * 32) is False

    @pytest.mark.parametrize("bad_id", ["../../etc", "..", "../x", "a/b", "foo", "", "ABCDEF" * 5 + "AB", "g" * 32])
    def test_purge_rejects_traversal_and_malformed_ids(self, tmp_path, bad_id):
        """A crafted item_id must never let purge escape the trash dir."""
        trash_dir = get_trash_dir(tmp_path, "workspace")
        # A canary living OUTSIDE the trash dir that a traversal id would hit.
        canary = tmp_path / "canary.txt"
        canary.write_bytes(b"do not touch")

        with pytest.raises(TrashItemNotFound):
            purge_trash_item(trash_dir, bad_id)

        assert canary.exists()
        assert canary.read_bytes() == b"do not touch"

    @pytest.mark.parametrize("bad_id", ["../../etc", "..", "../x", "a/b", "foo"])
    def test_restore_rejects_traversal_ids(self, tmp_path, bad_id):
        """Restore must reject malformed ids before touching the filesystem."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        trash_dir = get_trash_dir(tmp_path, "workspace")
        with pytest.raises(TrashItemNotFound):
            restore_trash_item(trash_dir, workspace, bad_id)

    def test_empty_trash_ignores_non_hex_sidecars(self, tmp_path):
        """A stray non-item .json in the trash dir does not abort empty_trash."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        trash_dir = get_trash_dir(tmp_path, "workspace")
        f = workspace / "real.txt"
        f.write_bytes(b"x")
        move_to_trash(trash_dir, f, "real.txt")
        # Drop a bogus sidecar whose stem is not a valid item id.
        (trash_dir / "notanid.json").write_text("{}")

        count = empty_trash(trash_dir)
        assert count == 1
        assert (trash_dir / "notanid.json").exists()  # left untouched

    def test_empty_trash_purges_everything(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        trash_dir = get_trash_dir(tmp_path, "workspace")
        for name in ("a.txt", "b.txt", "c.txt"):
            f = workspace / name
            f.write_bytes(b"x")
            move_to_trash(trash_dir, f, name)

        count = empty_trash(trash_dir)
        assert count == 3
        assert list_trash_items(trash_dir) == []


class TestUserWorkspaceTrashRoutes:
    """DELETE on /api/workspace/files must move-to-trash, not hard-delete."""

    @pytest.mark.asyncio
    async def test_delete_does_not_permanently_remove_bytes(self, client, app):
        content = b"do not vanish"
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("precious.txt", io.BytesIO(content), "text/plain")},
        )

        resp = await client.delete("/api/workspace/files/precious.txt")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Gone from the listing...
        listing = await client.get("/api/workspace/files")
        assert "precious.txt" not in [e["name"] for e in listing.json()]

        # ...but the bytes are still on disk in the trash, not wiped.
        trash_root = app.state.config_path.parent / ".taos-trash" / "workspace"
        found = list(trash_root.rglob("precious.txt"))
        assert len(found) == 1
        assert found[0].read_bytes() == content

    @pytest.mark.asyncio
    async def test_trash_list_restore_and_empty(self, client, app):
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("doc.txt", io.BytesIO(b"draft"), "text/plain")},
        )
        await client.delete("/api/workspace/files/doc.txt")

        listing = await client.get("/api/workspace/trash")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert len(items) == 1
        assert items[0]["original_path"] == "doc.txt"

        restore = await client.post(f"/api/workspace/trash/{items[0]['id']}/restore")
        assert restore.status_code == 200
        assert restore.json()["status"] == "restored"

        back = await client.get("/api/workspace/files")
        assert "doc.txt" in [e["name"] for e in back.json()]
        assert (await client.get("/api/workspace/trash")).json()["items"] == []

    @pytest.mark.asyncio
    async def test_empty_trash_purges_all_items(self, client, app):
        for name in ("one.txt", "two.txt"):
            await client.post(
                "/api/workspace/files/upload",
                files={"file": (name, io.BytesIO(b"x"), "text/plain")},
            )
            await client.delete(f"/api/workspace/files/{name}")

        empty_resp = await client.delete("/api/workspace/trash")
        assert empty_resp.status_code == 200
        assert empty_resp.json()["count"] == 2
        assert (await client.get("/api/workspace/trash")).json()["items"] == []

    @pytest.mark.asyncio
    async def test_restore_nonexistent_item_returns_404(self, client, app):
        resp = await client.post("/api/workspace/trash/ghost-id/restore")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_id", ["..", "%2e%2e", "not-hex", "deadbeef"])
    async def test_traversal_ids_are_rejected_by_routes(self, client, app, bad_id):
        """Purge/restore with a malformed or traversal id returns 404 and
        removes nothing outside the trash dir."""
        # Seed one real trashed item as a canary in the trash listing.
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("keep.txt", io.BytesIO(b"keep"), "text/plain")},
        )
        await client.delete("/api/workspace/files/keep.txt")

        purge = await client.delete(f"/api/workspace/trash/{bad_id}")
        assert purge.status_code == 404
        restore = await client.post(f"/api/workspace/trash/{bad_id}/restore")
        assert restore.status_code == 404

        # The genuine item is still present — nothing was clobbered.
        items = (await client.get("/api/workspace/trash")).json()["items"]
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_purge_single_item(self, client, app):
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("solo.txt", io.BytesIO(b"x"), "text/plain")},
        )
        await client.delete("/api/workspace/files/solo.txt")
        items = (await client.get("/api/workspace/trash")).json()["items"]
        item_id = items[0]["id"]

        resp = await client.delete(f"/api/workspace/trash/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "purged"
        assert (await client.get("/api/workspace/trash")).json()["items"] == []


class TestAgentWorkspaceTrashRoutes:
    """The agent workspace delete route mirrors the same move-to-trash fix."""

    def _add_agent(self, app, name: str) -> None:
        app.state.config.agents.append({
            "name": name,
            "host": "127.0.0.1",
            "qmd_index": "test",
            "color": "#cccccc",
        })

    @pytest.mark.asyncio
    async def test_delete_moves_to_trash_and_lists_restore(self, client, app):
        self._add_agent(app, "trashy")
        ws = app.state.agent_workspaces_dir / "trashy"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "agent_file.txt").write_bytes(b"agent data")

        resp = await client.delete("/api/agents/trashy/workspace/files/agent_file.txt")
        assert resp.status_code == 200
        assert not (ws / "agent_file.txt").exists()

        listing = await client.get("/api/agents/trashy/workspace/trash")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert len(items) == 1

        restore = await client.post(
            f"/api/agents/trashy/workspace/trash/{items[0]['id']}/restore"
        )
        assert restore.status_code == 200
        assert (ws / "agent_file.txt").read_bytes() == b"agent data"


class TestProjectFilesTrashRoutes:
    """The project files delete route mirrors the same move-to-trash fix."""

    @pytest.mark.asyncio
    async def test_delete_moves_to_trash_and_lists_restore(self, client, app):
        await client.post(
            "/api/projects/trash-proj/files/upload",
            files={"file": ("plan.txt", io.BytesIO(b"project plan"), "text/plain")},
        )

        resp = await client.delete("/api/projects/trash-proj/files/plan.txt")
        assert resp.status_code == 200

        listing = await client.get("/api/projects/trash-proj/trash")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert len(items) == 1
        assert items[0]["original_path"] == "plan.txt"

        restore = await client.post(
            f"/api/projects/trash-proj/trash/{items[0]['id']}/restore"
        )
        assert restore.status_code == 200

        back = await client.get("/api/projects/trash-proj/files")
        assert "plan.txt" in [e["name"] for e in back.json()]
