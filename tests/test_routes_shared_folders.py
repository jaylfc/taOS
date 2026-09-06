import pytest


class TestSharedFoldersRoutes:
    @pytest.mark.asyncio
    async def test_create_folder(self, client):
        resp = await client.post("/api/shared-folders", json={
            "name": "team-docs",
            "description": "Shared documentation",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    @pytest.mark.asyncio
    async def test_list_folders(self, client):
        await client.post("/api/shared-folders", json={"name": "list-test"})
        resp = await client.get("/api/shared-folders")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_delete_folder(self, client):
        resp = await client.post("/api/shared-folders", json={"name": "del-test"})
        folder_id = resp.json()["id"]
        resp = await client.delete(f"/api/shared-folders/{folder_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_grant_access(self, client):
        resp = await client.post("/api/shared-folders", json={"name": "access-test"})
        folder_id = resp.json()["id"]
        resp = await client.post(f"/api/shared-folders/{folder_id}/access", json={
            "agent_name": "test-agent",
            "permission": "readwrite",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "granted"
