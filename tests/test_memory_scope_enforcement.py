import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


@pytest.mark.asyncio
class TestMemoryScopeEnforcement:
    """Test that memory routes enforce memory_read and memory_write scopes.

    These tests drive the REAL HTTP routes through the app's auth middleware
    with a human session plus an agent Bearer token. The route handler then
    verifies the token's grants via check_agent_scope.
    """

    async def _mint(self, app, *, scopes=("a2a_receive",), project_id="prj-1"):
        registry = app.state.agent_registry
        grants = app.state.agent_grants
        if registry._db is None:
            await registry.init()
        if grants._db is None:
            await grants.init()
        priv, _pub = app.state.agent_registry_keypair
        import uuid
        unique_suffix = str(uuid.uuid4())[:8]
        rec = await registry.register(
            framework="grok",
            display_name="Grok",
            origin="external-selfjoin",
            handle=f"@grok-{unique_suffix}",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        for scope in scopes:
            await grants.add_grant(cid, scope, project_id=project_id)
        token = mint_registry_token(
            cid, priv, user_id="u", framework="grok", project_id=project_id
        )
        return cid, token

    async def test_memory_read_without_scope_is_refused(self, app, client):
        """Agent token WITHOUT memory_read must be REFUSED by the route."""
        cid, token = await self._mint(app, scopes=("a2a_receive",), project_id="prj-1")
        resp = await client.get(
            "/api/memory/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_memory_write_without_scope_is_refused(self, app, client):
        """Agent token WITHOUT memory_write must be REFUSED by the route."""
        cid, token = await self._mint(app, scopes=("memory_read",), project_id="prj-1")
        resp = await client.put(
            "/api/agents/grok/memory-config",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert resp.status_code == 403

    async def test_memory_read_with_scope_is_accepted(self, app, client):
        """Agent token WITH memory_read must be ACCEPTED by the route."""
        cid, token = await self._mint(app, scopes=("memory_read",), project_id="prj-1")
        resp = await client.get(
            "/api/memory/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    async def test_memory_write_with_scope_is_accepted(self, app, client):
        """Agent token WITH memory_write must be ACCEPTED by the route."""
        cid, token = await self._mint(app, scopes=("memory_write",), project_id="prj-1")
        resp = await client.put(
            "/api/agents/grok/memory-config",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert resp.status_code == 200
