"""RED-FIRST: memory routes must accept deployed agent's local token and fence it to its own namespace.

Every deployed agent calls the controller with its own TAOS_LOCAL_TOKEN (auth.mint_agent_local_token).
The auth middleware accepts that token and sets request.state.via = "local_token" and
request.state.agent_name = <agent> for bound tokens. Memory routes must check this FIRST
before calling check_agent_scope, which treats any Bearer as a registry JWT and fails.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _bound_client(app, agent_name: str) -> AsyncClient:
    """Return an unauthenticated AsyncClient presenting *agent_name*'s bound token."""
    token = app.state.auth.mint_agent_local_token(agent_name)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


def _host_token_client(app) -> AsyncClient:
    """Return a client presenting the host local token (no agent binding)."""
    token = app.state.auth.get_local_token()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


def _stub_http(app, responses: dict[str, dict]):
    """Install an AsyncMock http_client onto the app that returns the
    canned JSON body for matching path suffixes."""
    def _response_for(path_suffix: str) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=responses[path_suffix])
        return resp

    async def _get(url: str, *, params=None, timeout=None, **kw):
        for suffix, body in responses.items():
            if url.endswith(suffix):
                return _response_for(suffix)
        raise AssertionError(f"unexpected GET {url}")

    async def _post(url: str, *, json=None, timeout=None, **kw):
        for suffix, body in responses.items():
            if url.endswith(suffix):
                return _response_for(suffix)
        raise AssertionError(f"unexpected POST {url}")

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=_get)
    mock_client.post = AsyncMock(side_effect=_post)
    mock_client.aclose = AsyncMock(return_value=None)
    app.state.http_client = mock_client


@pytest_asyncio.fixture
async def local_token_app(app):
    """App with initialized stores and admin user set up."""
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

    yield app

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


@pytest.mark.asyncio
class TestMemoryLocalTokenFence:
    """Test that memory routes accept local tokens and fence bound agents to their own namespace."""

    async def test_bound_agent_search_other_agent_returns_403(self, local_token_app):
        """Bound agent-a searching for agent-b must return 403."""
        _stub_http(local_token_app, {
            "/search": {"results": []},
        })
        async with _bound_client(local_token_app, "agent-a") as c:
            resp = await c.post("/api/memory/search", json={"query": "x", "agent": "agent-b"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Agent can only search its own memory"

    async def test_bound_agent_search_own_namespace_works(self, local_token_app):
        """Bound agent-a searching without agent param uses its own namespace."""
        _stub_http(local_token_app, {
            "/search": {"results": []},
        })
        async with _bound_client(local_token_app, "agent-a") as c:
            resp = await c.post("/api/memory/search", json={"query": "x"})
        assert resp.status_code == 200
        # Verify the qmd call's dbPath ends with "agent-a/index.sqlite"
        mock_client = local_token_app.state.http_client
        call_args = mock_client.get.call_args
        assert call_args is not None
        # dbPath is passed in params
        params = call_args.kwargs.get("params", {})
        assert "agent-a/index.sqlite" in params.get("dbPath", "")

    async def test_bound_agent_browse_other_agent_returns_403(self, local_token_app):
        """Bound agent-a browsing agent-b must return 403."""
        _stub_http(local_token_app, {
            "/browse": {"chunks": []},
        })
        async with _bound_client(local_token_app, "agent-a") as c:
            resp = await c.get("/api/memory/browse?agent=agent-b")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Agent can only browse its own memory"

    async def test_bound_agent_browse_own_namespace_works(self, local_token_app):
        """Bound agent-a browsing without agent param uses its own namespace."""
        _stub_http(local_token_app, {
            "/browse": {"chunks": []},
        })
        async with _bound_client(local_token_app, "agent-a") as c:
            resp = await c.get("/api/memory/browse")
        assert resp.status_code == 200
        mock_client = local_token_app.state.http_client
        call_args = mock_client.get.call_args
        assert call_args is not None
        params = call_args.kwargs.get("params", {})
        assert "agent-a/index.sqlite" in params.get("dbPath", "")

    async def test_bound_agent_collections_other_agent_returns_403(self, local_token_app):
        """Bound agent-a accessing collections for agent-b must return 403."""
        _stub_http(local_token_app, {
            "/collections": [],
        })
        async with _bound_client(local_token_app, "agent-a") as c:
            resp = await c.get("/api/memory/collections/agent-b")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Agent can only access its own collections"

    async def test_bound_agent_collections_own_agent_works(self, local_token_app):
        """Bound agent-a accessing collections for itself works."""
        _stub_http(local_token_app, {
            "/collections": [],
        })
        async with _bound_client(local_token_app, "agent-a") as c:
            resp = await c.get("/api/memory/collections/agent-a")
        assert resp.status_code == 200

    async def test_bound_agent_delete_chunk_returns_403(self, local_token_app):
        """Bound agent delete-chunk must return 403 'Memory deletion is human-only'."""
        _stub_http(local_token_app, {
            "/delete-chunk": {"status": "ok"},
        })
        async with _bound_client(local_token_app, "agent-a") as c:
            resp = await c.delete("/api/memory/chunk/abc")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Memory deletion is human-only"

    async def test_host_token_search_other_agent_works(self, local_token_app):
        """Host token (unbound) searching for another agent works (admin path)."""
        _stub_http(local_token_app, {
            "/search": {"results": []},
        })
        async with _host_token_client(local_token_app) as c:
            resp = await c.post("/api/memory/search", json={"query": "x", "agent": "agent-b"})
        assert resp.status_code == 200

    async def test_host_token_browse_other_agent_works(self, local_token_app):
        """Host token (unbound) browsing another agent works (admin path)."""
        _stub_http(local_token_app, {
            "/browse": {"chunks": []},
        })
        async with _host_token_client(local_token_app) as c:
            resp = await c.get("/api/memory/browse?agent=agent-b")
        assert resp.status_code == 200

    async def test_host_token_collections_other_agent_works(self, local_token_app):
        """Host token (unbound) accessing collections for another agent works (admin path)."""
        _stub_http(local_token_app, {
            "/collections": [],
        })
        async with _host_token_client(local_token_app) as c:
            resp = await c.get("/api/memory/collections/agent-b")
        assert resp.status_code == 200