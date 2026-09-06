import pytest


@pytest.mark.asyncio
class TestSecretsPage:
    async def test_list_secrets_empty(self, client):
        resp = await client.get("/api/secrets")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_add_secret(self, client):
        resp = await client.post("/api/secrets", json={
            "name": "TEST_KEY",
            "value": "super-secret",
            "category": "api-keys",
            "description": "A test key",
            "agents": ["test-agent"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "id" in data

    async def test_get_secret_detail(self, client):
        await client.post("/api/secrets", json={
            "name": "DETAIL_KEY", "value": "the-value",
        })
        resp = await client.get("/api/secrets/DETAIL_KEY")
        assert resp.status_code == 200
        data = resp.json()
        assert data["value"] == "the-value"
        assert data["name"] == "DETAIL_KEY"

    async def test_list_secrets_masks_values(self, client):
        await client.post("/api/secrets", json={
            "name": "MASKED_KEY", "value": "should-be-hidden",
        })
        resp = await client.get("/api/secrets")
        assert resp.status_code == 200
        secrets = resp.json()
        assert len(secrets) >= 1
        for s in secrets:
            assert s.get("value") == "***"

    async def test_update_secret(self, client):
        await client.post("/api/secrets", json={
            "name": "UPD_KEY", "value": "old",
        })
        resp = await client.put("/api/secrets/UPD_KEY", json={
            "value": "new-value",
            "description": "updated desc",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        detail = await client.get("/api/secrets/UPD_KEY")
        assert detail.json()["value"] == "new-value"

    async def test_delete_secret(self, client):
        await client.post("/api/secrets", json={
            "name": "DEL_KEY", "value": "bye",
        })
        resp = await client.delete("/api/secrets/DEL_KEY")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        resp2 = await client.get("/api/secrets/DEL_KEY")
        assert resp2.status_code == 404

    async def test_get_nonexistent_secret(self, client):
        resp = await client.get("/api/secrets/NOPE")
        assert resp.status_code == 404

    async def test_delete_nonexistent_secret(self, client):
        resp = await client.delete("/api/secrets/NOPE")
        assert resp.status_code == 404

    async def test_add_duplicate_secret(self, client):
        await client.post("/api/secrets", json={
            "name": "DUP_KEY", "value": "first",
        })
        resp = await client.post("/api/secrets", json={
            "name": "DUP_KEY", "value": "second",
        })
        assert resp.status_code == 409

    async def test_list_categories(self, client):
        resp = await client.get("/api/secrets/categories")
        assert resp.status_code == 200
        names = {c["name"] for c in resp.json()}
        assert "api-keys" in names
        assert "general" in names

    async def test_agent_secrets(self, client):
        await client.post("/api/secrets", json={
            "name": "AGENT_SEC", "value": "val", "agents": ["test-agent"],
        })
        resp = await client.get("/api/secrets/agent/test-agent")
        assert resp.status_code == 200
        secrets = resp.json()
        assert len(secrets) == 1
        assert secrets[0]["name"] == "AGENT_SEC"
        assert secrets[0]["value"] == "val"

    async def test_update_nonexistent_secret(self, client):
        resp = await client.put("/api/secrets/NOPE", json={"value": "x"})
        assert resp.status_code == 404
