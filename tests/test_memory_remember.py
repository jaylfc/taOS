"""Tests for POST /api/memory/remember - agent memory write endpoint.

Stubs qmd serve the same way test_routes_memory.py does.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


def _stub_http(client_with_qmd, responses: dict[str, dict]):
    """Install an AsyncMock http_client onto the app that returns the
    canned JSON body for matching path suffixes. Keys are path suffixes
    like /browse or /search; values are the JSON body the endpoint should echo.
    """
    app = client_with_qmd._transport.app

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


def _make_client_with_token(app, token: str) -> AsyncClient:
    """Return an AsyncClient with the given token header."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
class TestMemoryRemember:
    async def _init_stores(self, app):
        """Initialize agent_registry and agent_grants stores if needed."""
        registry = app.state.agent_registry
        grants = app.state.agent_grants
        if registry._db is None:
            await registry.init()
        if grants._db is None:
            await grants.init()

    async def test_agent_token_no_memory_write_scope_gets_403(self, client_with_qmd):
        """Agent token without memory_write scope -> 403."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        app = client_with_qmd._transport.app
        await self._init_stores(app)

        # Create agent token with only a2a_receive
        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair

        rec = await registry.register(
            framework="test",
            display_name="Test Agent",
            origin="external-selfjoin",
            handle="@test-agent-remember",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        await grants.add_grant(cid, "a2a_receive", project_id="prj-1")
        token = mint_registry_token(cid, priv, user_id="u", framework="test", project_id="prj-1")

        async with _make_client_with_token(app, token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory"})
            assert resp.status_code == 403

    async def test_agent_token_with_memory_write_other_agent_gets_403(self, client_with_qmd):
        """Agent token WITH memory_write but body.agent=other -> 403."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        app = client_with_qmd._transport.app
        await self._init_stores(app)

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair

        rec = await registry.register(
            framework="test",
            display_name="Test Agent",
            origin="external-selfjoin",
            handle="@test-agent-remember2",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        await grants.add_grant(cid, "memory_write", project_id="prj-1")
        token = mint_registry_token(cid, priv, user_id="u", framework="test", project_id="prj-1")

        async with _make_client_with_token(app, token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": "other-agent"})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Agent can only write its own memory"

    async def test_agent_token_with_memory_write_omitted_agent_works(self, client_with_qmd):
        """Agent token WITH memory_write, body.agent omitted -> 200, dbPath ends with resolved name/index.sqlite."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        app = client_with_qmd._transport.app
        await self._init_stores(app)

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair

        rec = await registry.register(
            framework="test",
            display_name="Test Agent",
            origin="external-selfjoin",
            handle="@test-agent-remember3",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        await grants.add_grant(cid, "memory_write", project_id="prj-1")
        token = mint_registry_token(cid, priv, user_id="u", framework="test", project_id="prj-1")

        async with _make_client_with_token(app, token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["stored"] is True
            assert data["agent"]  # non-empty resolved agent name (canonical_id or config name)

    async def test_bound_local_token_other_agent_gets_403(self, client_with_qmd):
        """Bound local token for agent-a, agent=agent-b -> 403."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        app = client_with_qmd._transport.app
        token = app.state.auth.mint_agent_local_token("agent-a")
        async with _make_client_with_token(app, token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": "agent-b"})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Agent can only write its own memory"

    async def test_bound_local_token_omitted_agent_works(self, client_with_qmd):
        """Bound local token for agent-a, agent omitted -> 200 written to agent-a/index.sqlite."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        app = client_with_qmd._transport.app
        token = app.state.auth.mint_agent_local_token("agent-a")
        async with _make_client_with_token(app, token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["stored"] is True
            assert data["agent"] == "agent-a"

    async def test_admin_session_no_agent_gets_400(self, client_with_qmd):
        """Admin session (conftest client fixture), no agent -> 400."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        resp = await client_with_qmd.post("/api/memory/remember", json={"text": "test memory"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "agent is required"

    async def test_admin_session_with_agent_works(self, client_with_qmd):
        """Admin session, agent=agent-a -> 200."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        resp = await client_with_qmd.post("/api/memory/remember", json={"text": "test memory", "agent": "agent-a"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["stored"] is True
        assert data["agent"] == "agent-a"

    async def test_text_whitespace_only_gets_422(self, client_with_qmd):
        """Text with only whitespace -> 422."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        app = client_with_qmd._transport.app
        token = app.state.auth.mint_agent_local_token("agent-a")
        async with _make_client_with_token(app, token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "   "})
            assert resp.status_code == 422