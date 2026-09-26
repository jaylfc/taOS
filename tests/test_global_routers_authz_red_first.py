"""Red-first authorization tests for the routers fixed in tsk-miqdj6.

Each class covers one router module.  The member client is a non-admin
invited user with CSRF armed (mirrors ``test_global_routers_authz.py``) so a
403 here is the AUTHZ gate, never the CSRF gate -- every rejection asserts
the ``forbidden`` body to make that distinction explicit.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks

FORBIDDEN = {"detail": "forbidden"}


@pytest_asyncio.fixture(autouse=True)
async def _ensure_stores(client, tmp_path_factory):
    app = client._transport.app
    for attr in ("mcp_store", "agent_model_keys", "secrets", "metrics",
                 "notifications", "qmd_client", "scheduler", "channels",
                 "relationships", "conversion", "training", "agent_messages",
                 "shared_folders", "streaming_sessions", "expert_agents",
                 "chat_messages", "broker_store"):
        store = getattr(app.state, attr, None)
        if store is not None and hasattr(store, "_db") and store._db is None:
            try:
                await store.init()
            except Exception:
                pass
    yield


async def _invite_member(app) -> dict:
    auth_mgr = app.state.auth
    existing = auth_mgr.find_user("member")
    if existing:
        return existing
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite("member", invite_code, "Test Member", "", "memberauthzpass123")
    return auth_mgr.find_user("member")


async def _member_client(app, *, peer=None) -> AsyncClient:
    member = await _invite_member(app)
    token = app.state.auth.create_session(user_id=member["id"], long_lived=True)
    kwargs = {"app": app}
    if peer is not None:
        kwargs["client"] = peer
    return AsyncClient(
        transport=ASGITransport(**kwargs),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


# ---------------------------------------------------------------------------
# agents.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgentsMemberRejected:
    async def test_create_agent_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/agents", json={"name": "evil", "host": "1.2.3.4", "qmd_index": "x"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_update_agent_rejected(self, client, app):
        registry = app.state.agent_registry
        if registry._db is None:
            await registry.init()
        admin_uid = app.state.auth.find_user("admin")["id"]
        rec = await registry.register(framework="taosmd", display_name="Owner Bot", handle="@owner-bot", user_id=admin_uid)
        member = await _member_client(app)
        try:
            resp = await member.put(f"/api/agents/{rec['canonical_id']}", json={"host": "5.6.7.8"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestAgentsAdminAllowed:
    async def test_create_agent_allowed(self, client, app):
        resp = await client.post("/api/agents", json={"name": "admin-bot", "host": "1.2.3.4", "qmd_index": "x"})
        assert resp.status_code == 200, resp.text

    async def test_list_agents_open(self, client, app):
        resp = await client.get("/api/agents")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# account_proxy.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAccountProxyMemberRejected:
    async def test_subdomain_claim_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/account/subdomains/claim", json={"name": "evil"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_hub_request_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/account/hub/requests", json={})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestAccountProxyAdminAllowed:
    async def test_subdomain_claim_allowed(self, client, app):
        with patch("tinyagentos.routes.account_proxy._base_url", return_value="http://localhost:9999"):
            with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=MagicMock(status_code=200, content=b"{}", headers={}, json=lambda: {})):
                resp = await client.post("/api/account/subdomains/claim", json={"name": "admin-test"})
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# chat_admin.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatAdminMemberRejected:
    async def test_create_channel_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/chat/channels", json={"name": "evil"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestChatAdminAdminAllowed:
    async def test_create_channel_allowed(self, client, app):
        resp = await client.post("/api/chat/channels", json={"name": "admin-channel"})
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# providers.py (remaining ungated endpoints)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProvidersMemberRejected:
    async def test_providers_test_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/providers/test", json={"url": "http://localhost:9"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_providers_refresh_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/providers/models/refresh")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestProvidersAdminAllowed:
    async def test_add_provider_allowed(self, client, app):
        with patch("tinyagentos.routes.providers.save_config_locked", new=AsyncMock()):
            resp = await client.post("/api/providers", json={"name": "test", "type": "llama-cpp", "url": "http://localhost:9"})
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# models.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestModelsMemberRejected:
    async def test_download_model_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/models/download", json={"app_id": "x", "variant_id": "y"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestModelsAdminAllowed:
    async def test_list_models_open(self, client, app):
        resp = await client.get("/api/models")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# github_oauth.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGithubOAuthMemberRejected:
    async def test_device_start_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/github/oauth/device/start")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_delete_identity_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.delete("/api/github/identities/some-id")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestGithubOAuthAdminAllowed:
    async def test_list_identities_open(self, client, app):
        store = MagicMock()
        store.list = AsyncMock(return_value=[])
        client._transport.app.state.github_identities = store
        resp = await client.get("/api/github/identities")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# memory_management.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMemoryManagementMemberRejected:
    async def test_update_memory_settings_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.put("/api/memory/settings", json={"backend": "taosmd"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestMemoryManagementAdminAllowed:
    async def test_memory_stats_allowed(self, client, app):
        resp = await client.get("/api/memory/stats")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# knowledge_graph.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestKnowledgeGraphMemberRejected:
    async def test_add_entity_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/kg/entities", json={"name": "x", "type": "test"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestKnowledgeGraphAdminAllowed:
    async def test_list_entities_allowed(self, client, app):
        graph = MagicMock()
        graph.list_entities = AsyncMock(return_value=[])
        client._transport.app.state.knowledge_graph = graph
        resp = await client.get("/api/kg/entities")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# cluster_migrate.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestClusterMigrateMemberRejected:
    async def test_add_remote_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/cluster/remotes", json={"name": "r", "url": "http://x", "token": "t"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestClusterMigrateAdminAllowed:
    async def test_list_remotes_allowed(self, client, app):
        with patch("tinyagentos.routes.cluster_migrate.remote_list", new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/api/cluster/remotes")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# store.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStoreMemberRejected:
    async def test_install_app_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/store/install", json={"app_id": "x"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestStoreAdminAllowed:
    async def test_list_catalog_open(self, client, app):
        resp = await client.get("/api/store/catalog")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# tasks.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTasksMemberRejected:
    async def test_create_task_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/tasks", json={"name": "x", "schedule": "* * * * *", "command": "echo hi"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestTasksAdminAllowed:
    async def test_list_tasks_allowed(self, client, app):
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# desktop.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDesktopMemberRejected:
    async def test_update_desktop_settings_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.put("/api/desktop/settings", json={"theme": "dark"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestDesktopAdminAllowed:
    async def test_get_desktop_settings_allowed(self, client, app):
        store = MagicMock()
        store.get_settings = AsyncMock(return_value={})
        client._transport.app.state.desktop_settings = store
        resp = await client.get("/api/desktop/settings")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# agent_browsers.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgentBrowsersMemberRejected:
    async def test_create_profile_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post("/api/agent-browsers/profiles", json={"profile_name": "x"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestAgentBrowsersAdminAllowed:
    async def test_list_profiles_allowed(self, client, app):
        mgr = MagicMock()
        mgr.list_profiles = AsyncMock(return_value=[])
        client._transport.app.state.agent_browsers = mgr
        resp = await client.get("/api/agent-browsers/profiles")
        assert resp.status_code == 200, resp.text
