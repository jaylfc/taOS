import pytest


@pytest.mark.asyncio
class TestRelationshipsRoutes:
    async def test_list_groups_empty(self, client):
        resp = await client.get("/api/relationships/groups")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_group(self, client):
        resp = await client.post("/api/relationships/groups", json={
            "name": "research",
            "description": "Research team",
            "color": "#00ff00",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "id" in data

    async def test_create_duplicate_group(self, client):
        await client.post("/api/relationships/groups", json={"name": "dup-group"})
        resp = await client.post("/api/relationships/groups", json={"name": "dup-group"})
        assert resp.status_code == 409

    async def test_list_groups_with_members(self, client):
        resp = await client.post("/api/relationships/groups", json={"name": "squad"})
        gid = resp.json()["id"]
        await client.post(f"/api/relationships/groups/{gid}/members", json={
            "agent_name": "test-agent", "role": "lead",
        })
        resp = await client.get("/api/relationships/groups")
        groups = resp.json()
        assert len(groups) == 1
        assert groups[0]["name"] == "squad"
        assert len(groups[0]["members"]) == 1
        assert groups[0]["members"][0]["agent_name"] == "test-agent"

    async def test_update_group(self, client):
        resp = await client.post("/api/relationships/groups", json={"name": "old"})
        gid = resp.json()["id"]
        resp = await client.put(f"/api/relationships/groups/{gid}", json={
            "name": "new", "description": "updated",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_delete_group(self, client):
        resp = await client.post("/api/relationships/groups", json={"name": "bye"})
        gid = resp.json()["id"]
        resp = await client.delete(f"/api/relationships/groups/{gid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    async def test_delete_nonexistent_group(self, client):
        resp = await client.delete("/api/relationships/groups/9999")
        assert resp.status_code == 404

    async def test_add_member(self, client):
        resp = await client.post("/api/relationships/groups", json={"name": "team"})
        gid = resp.json()["id"]
        resp = await client.post(f"/api/relationships/groups/{gid}/members", json={
            "agent_name": "test-agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "added"

    async def test_remove_member(self, client):
        resp = await client.post("/api/relationships/groups", json={"name": "team2"})
        gid = resp.json()["id"]
        await client.post(f"/api/relationships/groups/{gid}/members", json={
            "agent_name": "test-agent",
        })
        resp = await client.delete(f"/api/relationships/groups/{gid}/members/test-agent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    async def test_get_agent_info(self, client):
        resp = await client.post("/api/relationships/groups", json={"name": "info-grp"})
        gid = resp.json()["id"]
        await client.post(f"/api/relationships/groups/{gid}/members", json={
            "agent_name": "test-agent",
        })
        resp = await client.get("/api/relationships/agent/test-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert "groups" in data
        assert "permissions" in data
        assert len(data["groups"]) == 1

    async def test_set_permission(self, client):
        resp = await client.post("/api/relationships/permissions", json={
            "from_agent": "test-agent", "to_agent": "other-agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "granted"

    async def test_revoke_permission(self, client):
        await client.post("/api/relationships/permissions", json={
            "from_agent": "test-agent", "to_agent": "other-agent",
        })
        resp = await client.request("DELETE", "/api/relationships/permissions", json={
            "from_agent": "test-agent", "to_agent": "other-agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    async def test_permission_reflected_in_agent_info(self, client):
        await client.post("/api/relationships/permissions", json={
            "from_agent": "test-agent", "to_agent": "other-agent",
        })
        resp = await client.get("/api/relationships/agent/test-agent")
        data = resp.json()
        assert "other-agent" in data["permissions"]["can_reach"]
