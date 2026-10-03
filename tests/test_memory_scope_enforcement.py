import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.routes.agent_auth_requests import VALID_SCOPES
from tinyagentos.routes.agent_registry import _ALLOWED_SCOPES


@pytest_asyncio.fixture
async def agent_token_app(app):
    """App with initialized stores and an agent token without memory scopes."""
    for attr in ("agent_registry", "agent_grants", "metrics", "notifications", "qmd_client", 
                 "secrets", "broker_store", "scheduler", "channels", "relationship_mgr",
                 "conversion_mgr", "training_mgr", "agent_messages", "shared_folders",
                 "streaming_sessions", "expert_agents", "chat_messages", "chat_channels",
                 "project_store", "project_invites", "board_audit", "receipt_store",
                 "task_strikes", "project_task_store", "project_element_store",
                 "project_notes_store", "project_lists_store", "project_list_entries_store",
                 "routine_store", "decision_store", "execution_policies", "coding_session_store",
                 "container_request_store", "canvas_store", "themes", "office_docs", "web_sites",
                 "song_store", "lora_store", "design_docs", "app_grants", "license_acceptances",
                 "feedback_store", "client_log_store", "device_store", "device_pair_requests",
                 "council_roles", "council_members"):
        store = getattr(app.state, attr, None)
        if store is not None and hasattr(store, '_db') and store._db is None:
            if hasattr(store, 'init'):
                await store.init()
    
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    app.state._startup_complete = True
    
    # Register agent WITHOUT memory_read/memory_write scopes
    registry = app.state.agent_registry
    grants = app.state.agent_grants
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
    # Only grant a2a_receive, NOT memory_read or memory_write
    await grants.add_grant(cid, "a2a_receive", project_id="prj-1")
    token = mint_registry_token(
        cid, priv, user_id="u", framework="grok", project_id="prj-1"
    )
    
    yield app, token, cid
    
    # Cleanup
    for attr in ("agent_registry", "agent_grants", "metrics", "notifications", "qmd_client", 
                 "secrets", "broker_store", "scheduler", "channels", "relationship_mgr",
                 "conversion_mgr", "training_mgr", "agent_messages", "shared_folders",
                 "streaming_sessions", "expert_agents", "chat_messages", "chat_channels",
                 "project_store", "project_invites", "board_audit", "receipt_store",
                 "task_strikes", "project_task_store", "project_element_store",
                 "project_notes_store", "project_lists_store", "project_list_entries_store",
                 "routine_store", "decision_store", "execution_policies", "coding_session_store",
                 "container_request_store", "canvas_store", "themes", "office_docs", "web_sites",
                 "song_store", "lora_store", "design_docs", "app_grants", "license_acceptances",
                 "feedback_store", "client_log_store", "device_store", "device_pair_requests",
                 "council_roles", "council_members"):
        store = getattr(app.state, attr, None)
        if store is not None and hasattr(store, '_db') and store._db is not None:
            if hasattr(store, 'close'):
                await store.close()


class TestMemoryScopeEnforcement:
    """Test that memory scopes have been added to the grantable vocabulary (feature implementation)
    
    This replaces the previous test that memory_read was removed. Now memory_read is
    the least-privilege scope that allows agents to access their own memory index.
    """

    def test_memory_read_added_to_valid_scopes(self):
        """memory_read must now be in the grantable vocabulary."""
        assert "memory_read" in VALID_SCOPES

    def test_memory_write_removed_from_valid_scopes(self):
        """memory_write must still not be in the grantable vocabulary (stays human-only)."""
        assert "memory_write" not in VALID_SCOPES

    def test_tools_execute_removed_from_valid_scopes(self):
        """tools_execute must not be in the grantable vocabulary."""
        assert "tools_execute" not in VALID_SCOPES

    def test_memory_read_added_to_allowed_scopes(self):
        """memory_read should be in the internal agent allowed scopes after being added to valid scopes."""
        # _ALLOWED_SCOPES is an internal filter for agent scopes
        # memory_read should be allowed through this filter since it's in VALID_SCOPES
        assert "memory_read" in _ALLOWED_SCOPES

    def test_memory_write_removed_from_allowed_scopes(self):
        """memory_write must not be in the internal agent allowed scopes."""
        assert "memory_write" not in _ALLOWED_SCOPES

    def test_tools_execute_removed_from_allowed_scopes(self):
        """tools_execute must not be in the internal agent allowed scopes."""
        assert "tools_execute" not in _ALLOWED_SCOPES

    @pytest.mark.asyncio
    async def test_memory_routes_enforce_memory_read_scope(self, agent_token_app):
        """Memory routes now enforce memory_read scope for registry-JWT agents."""
        app, token, cid = agent_token_app
        
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            # Agent token without memory_read scope should get 403
            resp = await client.get("/api/memory/browse")
            assert resp.status_code == 403
            assert resp.json()["detail"] == "token does not hold an active 'memory_read' grant"
            
            resp = await client.post("/api/memory/search", json={"query": "test", "mode": "keyword"})
            assert resp.status_code == 403
            
            resp = await client.get("/api/memory/collections/alpha")
            assert resp.status_code == 403
            
            # DELETE /api/memory/chunk/{content_hash} should stay human-only
            resp = await client.delete("/api/memory/chunk/abc123")
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_memory_routes_reachable_without_scope_checks(self, agent_token_app):
        """Memory routes are reachable by agent tokens if they have memory_read scope.
        
        This test has been updated from the original consent-integrity fix that
        blocked agent tokens from reaching memory routes. Now memory routes are
        reachable but enforce the memory_read scope.
        """
        app, token, cid = agent_token_app
        
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            # Agent tokens without memory_read scope get 403 (not 401 like before)
            # The test agent token in agent_token_app fixture does NOT have memory_read
            resp = await client.get("/api/memory/browse")
            assert resp.status_code == 403
            assert resp.json()["detail"] == "token does not hold an active 'memory_read' grant"

    @pytest.mark.asyncio
    async def test_memory_write_routes_reachable_without_scope_checks(self, agent_token_app):
        """Memory write routes should also be blocked at middleware for agent tokens."""
        app, token, cid = agent_token_app
        
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            # User memory routes should still be blocked by middleware (401)
            resp = await client.post("/api/user-memory/save", json={"content": "test"})
            assert resp.status_code == 401
            
            resp = await client.post("/api/memory/recipes/default/apply", json={})
            assert resp.status_code == 401
            
            # DELETE /api/memory/chunk/{content_hash} is now reachable by agent tokens
            # (in _MEMORY_ROUTES) but should return 403 from the handler (human-only)
            resp = await client.delete("/api/memory/chunk/abc123")
            assert resp.status_code == 403
            assert resp.json()["detail"] == "token does not hold an active 'memory_read' grant"
            
            # DELETE /api/user-memory/chunk/{content_hash} should stay human-only (401)
            resp = await client.delete("/api/user-memory/chunk/abc123")
            assert resp.status_code == 401