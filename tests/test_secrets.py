"""Tests for SecretsStore and the secrets routes, including the new
github-installation category and get_agent_github_installations."""

from __future__ import annotations

import json

import pytest


class TestGitHubInstallationCategory:
    """Tests for the github-installation secret category."""

    @pytest.mark.asyncio
    async def test_github_installation_in_categories(self, client):
        """The github-installation category appears in the categories list."""
        resp = await client.get("/api/secrets/categories")
        assert resp.status_code == 200
        categories = [c["name"] for c in resp.json()]
        assert "github-installation" in categories

    @pytest.mark.asyncio
    async def test_create_github_installation_secret(self, client):
        """Creating a secret with category github-installation works."""
        payload = {
            "name": "owner/repo",
            "value": json.dumps({"installation_id": 42, "repo_full_name": "owner/repo", "permissions": ["contents:read"]}),
            "category": "github-installation",
            "description": "Test GitHub App installation",
            "agents": ["test-agent"],
        }
        resp = await client.post("/api/secrets", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] is not None
        assert data["status"] == "created"

    @pytest.mark.asyncio
    async def test_list_secrets_by_github_installation_category(self, client):
        """Listing secrets filtered by github-installation category works."""
        await client.post("/api/secrets", json={
            "name": "owner/repo2",
            "value": json.dumps({"installation_id": 43, "repo_full_name": "owner/repo2", "permissions": []}),
            "category": "github-installation",
            "description": "Another test",
            "agents": ["test-agent"],
        })

        resp = await client.get("/api/secrets", params={"category": "github-installation"})
        assert resp.status_code == 200
        secrets = resp.json()
        assert any(s["name"] == "owner/repo2" for s in secrets)

    @pytest.mark.asyncio
    async def test_get_agent_github_installations_endpoint(self, client):
        """The GET /api/secrets/agent/{name}/github endpoint returns installations."""
        await client.post("/api/secrets", json={
            "name": "org/repo-a",
            "value": json.dumps({"installation_id": 99, "repo_full_name": "org/repo-a", "permissions": ["contents:read"]}),
            "category": "github-installation",
            "description": "Installation A",
            "agents": ["code-reviewer"],
        })

        resp = await client.get("/api/secrets/agent/code-reviewer/github")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(inst["repo_full_name"] == "org/repo-a" and inst["installation_id"] == 99 for inst in data)

    @pytest.mark.asyncio
    async def test_get_agent_github_installations_no_grants(self, client):
        """An agent with no grants returns an empty list."""
        resp = await client.get("/api/secrets/agent/nonexistent-agent/github")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_github_grants_requires_auth(self, client, app):
        """Unauthenticated request to the GitHub grants endpoint returns 401."""
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            # No taos_session cookie — simulate unauthenticated caller.
        ) as unauth_client:
            resp = await unauth_client.get("/api/secrets/agent/test-agent/github")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_duplicate_secret_rejected(self, client):
        """Creating a secret with a duplicate name returns 409."""
        await client.post("/api/secrets", json={
            "name": "dup/repo",
            "value": "test",
            "category": "github-installation",
            "description": "",
            "agents": [],
        })
        resp = await client.post("/api/secrets", json={
            "name": "dup/repo",
            "value": "test2",
            "category": "github-installation",
            "description": "",
            "agents": [],
        })
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_update_secret_agents(self, client):
        """Updating a secret's agent list works."""
        await client.post("/api/secrets", json={
            "name": "update-test/repo",
            "value": json.dumps({"installation_id": 77, "repo_full_name": "update-test/repo", "permissions": []}),
            "category": "github-installation",
            "description": "",
            "agents": [],
        })

        # Names containing / are now supported via {name:path} route converters.
        resp = await client.put("/api/secrets/update-test/repo", json={
            "agents": ["agent-1", "agent-2"],
        })
        assert resp.status_code == 200


class TestSecretsEncryption:
    """Encryption round-trip and migration tests (XOR ↔ Fernet)."""

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_round_trip(self, client):
        """A secret written via the API can be read back with its original value."""
        secret_payload = {
            "name": "enc-roundtrip",
            "value": "super-secret-token-abc123",
            "category": "general",
            "description": "Round-trip test",
            "agents": [],
        }
        await client.post("/api/secrets", json=secret_payload)

        resp = await client.get("/api/secrets/enc-roundtrip")
        assert resp.status_code == 200
        data = resp.json()
        # The stored value should be the plaintext we wrote (decrypted on read).
        assert data["value"] == "super-secret-token-abc123"

    @pytest.mark.asyncio
    async def test_list_masks_values(self, client):
        """Listing secrets masks values so plaintext is never leaked in bulk."""
        await client.post("/api/secrets", json={
            "name": "masked-secret",
            "value": "should-not-leak",
            "category": "general",
            "description": "",
            "agents": [],
        })
        resp = await client.get("/api/secrets")
        assert resp.status_code == 200
        secrets = resp.json()
        # All values in the list view should be masked.
        for s in secrets:
            assert s["value"] == "***"

    @pytest.mark.asyncio
    async def test_update_reencrypts(self, client):
        """Updating a secret's value encrypts the new value correctly."""
        await client.post("/api/secrets", json={
            "name": "reencrypt-test",
            "value": "original-value",
            "category": "general",
            "description": "",
            "agents": [],
        })
        await client.put("/api/secrets/reencrypt-test", json={
            "value": "updated-value",
        })
        resp = await client.get("/api/secrets/reencrypt-test")
        assert resp.status_code == 200
        assert resp.json()["value"] == "updated-value"


class TestWriteOnceSecrets:
    """write_once pins a secret's value so it cannot silently rotate.

    Rotation for the taosmd archive key is a NEW secret name (new key id),
    never an in-place value change.  Metadata edits still work, and an
    explicit force=true admin query is required to delete one.
    """

    @pytest.mark.asyncio
    async def test_create_write_once(self, client):
        """A secret created with write_once=true is flagged on read-back."""
        await client.post("/api/secrets", json={
            "name": "Pinned-Key",
            "value": "pinned-value-1",
            "category": "credentials",
            "description": "must not rotate",
            "agents": [],
            "write_once": True,
        })
        resp = await client.get("/api/secrets/Pinned-Key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["write_once"] is True
        assert data["value"] == "pinned-value-1"

    @pytest.mark.asyncio
    async def test_put_value_refused_and_stored_value_unchanged(self, client):
        """PUT on a write_once secret 409s and leaves the pinned value intact."""
        await client.post("/api/secrets", json={
            "name": "Pinned-Key",
            "value": "pinned-value-1",
            "category": "credentials",
            "description": "",
            "agents": [],
            "write_once": True,
        })
        resp = await client.put("/api/secrets/Pinned-Key", json={
            "value": "pinned-value-2",
        })
        assert resp.status_code == 409
        assert "write_once" in resp.json()["error"].lower()
        # Read it back: the value is still the original.
        detail = await client.get("/api/secrets/Pinned-Key")
        assert detail.status_code == 200
        assert detail.json()["value"] == "pinned-value-1"

    @pytest.mark.asyncio
    async def test_put_description_only_succeeds(self, client):
        """Metadata edits on a write_once secret still return 200."""
        await client.post("/api/secrets", json={
            "name": "Pinned-Key",
            "value": "pinned-value-1",
            "category": "credentials",
            "description": "old desc",
            "agents": [],
            "write_once": True,
        })
        resp = await client.put("/api/secrets/Pinned-Key", json={
            "description": "new desc",
        })
        assert resp.status_code == 200
        detail = await client.get("/api/secrets/Pinned-Key")
        assert detail.json()["description"] == "new desc"
        assert detail.json()["value"] == "pinned-value-1"

    @pytest.mark.asyncio
    async def test_delete_without_force_refused(self, client):
        """DELETE of a write_once secret 409s and the secret stays present."""
        await client.post("/api/secrets", json={
            "name": "Pinned-Key",
            "value": "pinned-value-1",
            "category": "credentials",
            "description": "",
            "agents": [],
            "write_once": True,
        })
        resp = await client.delete("/api/secrets/Pinned-Key")
        assert resp.status_code == 409
        assert "write_once" in resp.json()["error"].lower()
        still = await client.get("/api/secrets/Pinned-Key")
        assert still.status_code == 200
        assert still.json()["value"] == "pinned-value-1"

    @pytest.mark.asyncio
    async def test_delete_with_force_removes(self, client):
        """DELETE with force=true removes a write_once secret."""
        await client.post("/api/secrets", json={
            "name": "Pinned-Key",
            "value": "pinned-value-1",
            "category": "credentials",
            "description": "",
            "agents": [],
            "write_once": True,
        })
        resp = await client.delete("/api/secrets/Pinned-Key?force=true")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        gone = await client.get("/api/secrets/Pinned-Key")
        assert gone.status_code == 404

    @pytest.mark.asyncio
    async def test_non_write_once_behaves_as_before(self, client):
        """A secret created without write_once accepts a value PUT (200)."""
        await client.post("/api/secrets", json={
            "name": "Mutable-Key",
            "value": "original",
            "category": "general",
            "description": "",
            "agents": [],
        })
        resp = await client.put("/api/secrets/Mutable-Key", json={
            "value": "rotated",
        })
        assert resp.status_code == 200
        detail = await client.get("/api/secrets/Mutable-Key")
        assert detail.json()["value"] == "rotated"
        assert detail.json()["write_once"] is False

    @pytest.mark.asyncio
    async def test_write_once_default_false(self, client):
        """write_once defaults to false, so nothing existing changes."""
        await client.post("/api/secrets", json={
            "name": "Default-Key",
            "value": "v",
            "category": "general",
            "description": "",
            "agents": [],
        })
        detail = await client.get("/api/secrets/Default-Key")
        assert detail.json()["write_once"] is False
