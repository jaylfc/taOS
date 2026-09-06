"""Endpoint tests for tinyagentos/routes/user_personas.py."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestListPersonas:
    async def test_empty_list(self, client):
        resp = await client.get("/api/user-personas")
        assert resp.status_code == 200
        data = resp.json()
        assert "personas" in data
        assert isinstance(data["personas"], list)
        assert data["personas"] == []

    async def test_list_returns_created_personas(self, client):
        created = await client.post(
            "/api/user-personas",
            json={"name": "helper", "soul_md": "be helpful", "agent_md": "assist"},
        )
        assert created.status_code == 201
        resp = await client.get("/api/user-personas")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["personas"]) == 1
        assert data["personas"][0]["name"] == "helper"


@pytest.mark.asyncio
class TestCreatePersona:
    async def test_create_returns_201(self, client):
        resp = await client.post(
            "/api/user-personas",
            json={"name": "tester", "soul_md": "test soul", "agent_md": "test agent"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert isinstance(data["id"], str)

    async def test_create_persists_fields(self, client):
        resp = await client.post(
            "/api/user-personas",
            json={
                "name": "fulldetail",
                "soul_md": "soul content",
                "agent_md": "agent content",
                "description": "a test persona",
            },
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]
        get_resp = await client.get(f"/api/user-personas/{pid}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["name"] == "fulldetail"
        assert data["soul_md"] == "soul content"
        assert data["agent_md"] == "agent content"
        assert data["description"] == "a test persona"

    async def test_create_minimal_payload(self, client):
        resp = await client.post(
            "/api/user-personas",
            json={"name": "minimal"},
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]
        data = (await client.get(f"/api/user-personas/{pid}")).json()
        assert data["name"] == "minimal"
        assert data["soul_md"] == ""
        assert data["agent_md"] == ""


@pytest.mark.asyncio
class TestGetPersona:
    async def test_get_existing(self, client):
        created = await client.post(
            "/api/user-personas",
            json={"name": "getme", "soul_md": "soul"},
        )
        pid = created.json()["id"]
        resp = await client.get(f"/api/user-personas/{pid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == pid
        assert data["name"] == "getme"
        assert "soul_md" in data
        assert "agent_md" in data
        assert "created_at" in data

    async def test_get_not_found(self, client):
        resp = await client.get("/api/user-personas/nonexistentid123")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data


@pytest.mark.asyncio
class TestUpdatePersona:
    async def test_update_name(self, client):
        created = await client.post(
            "/api/user-personas",
            json={"name": "old-name"},
        )
        pid = created.json()["id"]
        resp = await client.patch(
            f"/api/user-personas/{pid}",
            json={"name": "new-name"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        updated = (await client.get(f"/api/user-personas/{pid}")).json()
        assert updated["name"] == "new-name"

    async def test_update_soul_and_agent(self, client):
        created = await client.post(
            "/api/user-personas",
            json={"name": "updateme", "soul_md": "old soul"},
        )
        pid = created.json()["id"]
        resp = await client.patch(
            f"/api/user-personas/{pid}",
            json={
                "soul_md": "new soul",
                "agent_md": "new agent",
                "description": "updated",
            },
        )
        assert resp.status_code == 200
        updated = (await client.get(f"/api/user-personas/{pid}")).json()
        assert updated["soul_md"] == "new soul"
        assert updated["agent_md"] == "new agent"
        assert updated["description"] == "updated"

    async def test_update_not_found(self, client):
        resp = await client.patch(
            "/api/user-personas/nonexistentid123",
            json={"name": "ghost"},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data


@pytest.mark.asyncio
class TestDeletePersona:
    async def test_delete_existing(self, client):
        created = await client.post(
            "/api/user-personas",
            json={"name": "deleteme"},
        )
        pid = created.json()["id"]
        resp = await client.delete(f"/api/user-personas/{pid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        get_resp = await client.get(f"/api/user-personas/{pid}")
        assert get_resp.status_code == 404

    async def test_delete_not_found_still_returns_200(self, client):
        resp = await client.delete("/api/user-personas/nonexistentid123")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
