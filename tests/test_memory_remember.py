"""Tests for POST /api/memory/remember - agent memory write endpoint.

Stubs qmd serve the same way test_routes_memory.py does.
"""
from __future__ import annotations

import secrets
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.auth import hash_password


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
        event_hooks=csrf_event_hooks(),
    )


def _add_non_admin_user(auth_mgr, username, password):
    """Add a non-admin user directly to the auth user store."""
    data = auth_mgr._read_users()
    uid = secrets.token_urlsafe(8)
    record = {
        "id": uid,
        "username": username,
        "full_name": "Regular User",
        "email": "",
        "password_hash": hash_password(password),
        "created_at": int(time.time()),
        "is_admin": False,
    }
    data.setdefault("users", []).append(record)
    auth_mgr._write_users(data)
    return uid


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
        config = app.state.config

        rec = await registry.register(
            framework="test",
            display_name="Test Agent",
            origin="external-selfjoin",
            handle="@test-agent-remember3",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        await grants.add_grant(cid, "memory_write", project_id="prj-1")

        # Add agent to config with matching registry_canonical_id (required for registry JWT path)
        # Config agent name must match handle (without @) for _resolve_agent_name to find it
        from tinyagentos.agent_db import find_agent
        if find_agent(config, "test-agent-remember3") is None:
            config.agents.append({
                "name": "test-agent-remember3",
                "display_name": "Test Agent",
                "host": "",
                "qmd_index": "test-agent-remember3",
                "color": "#888",
                "registry_canonical_id": cid,
            })

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

    async def test_admin_session_empty_agent_gets_400(self, client_with_qmd):
        """Admin session (conftest client fixture), empty or whitespace agent -> 400."""
        _stub_http(client_with_qmd, {
            "/ingest": {"status": "ok"},
        })

        # Empty string agent
        resp = await client_with_qmd.post("/api/memory/remember", json={"text": "x", "agent": ""})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "agent is required"

        # Whitespace agent
        resp = await client_with_qmd.post("/api/memory/remember", json={"text": "x", "agent": "   "})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "agent is required"

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

    # --- Security fix tests (tsk-quaat4) ---

    async def test_non_admin_user_cannot_write_other_users_agent_memory(self, client_with_qmd):
        """Non-admin user session writing to another user's agent -> 403; owner and admin still get 200."""
        _stub_http(client_with_qmd, {"/ingest": {"status": "ok"}})

        app = client_with_qmd._transport.app
        await self._init_stores(app)

        auth = app.state.auth
        alice_record = auth.find_user("admin")
        alice_uid = alice_record["id"] if alice_record else ""

        # Create bob as a non-admin user
        bob_uid = _add_non_admin_user(auth, "bob", "bobpass")

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair

        # Create an agent owned by alice (admin)
        rec_alice = await registry.register(
            framework="test",
            display_name="Alice's Agent",
            origin="external-selfjoin",
            handle="@alice-agent",
            user_id=alice_uid,
        )
        cid_alice = rec_alice["canonical_id"]
        await registry.set_status(cid_alice, "active")
        await grants.add_grant(cid_alice, "memory_write", project_id="prj-1")

        # Bob (non-admin) tries to write to Alice's agent -> 403
        bob_token = auth.create_session(user_id=bob_uid, long_lived=True)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": cid_alice})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "forbidden"

        # Alice (admin) can write to her own agent -> 200
        alice_token = auth.create_session(user_id=alice_uid, long_lived=True)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": alice_token},
            event_hooks=csrf_event_hooks(),
        ) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": cid_alice})
            assert resp.status_code == 200
            assert resp.json()["stored"] is True

    async def test_registry_jwt_canonical_id_mismatch_gets_403(self, client_with_qmd):
        """Registry JWT with display_name matching config agent's name but different canonical_id -> 403; matching identity gets 200."""
        _stub_http(client_with_qmd, {"/ingest": {"status": "ok"}})

        app = client_with_qmd._transport.app
        await self._init_stores(app)

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair
        config = app.state.config

        # Register a deployed config agent with registry_canonical_id
        rec_config = await registry.register(
            framework="test",
            display_name="Config Agent Display",
            origin="taos-deployed",
            handle="@config-agent",
            user_id="user-123",
        )
        cid_config = rec_config["canonical_id"]
        await grants.add_grant(cid_config, "memory_write", project_id="prj-1")

        # Add the agent to config with registry_canonical_id
        from tinyagentos.agent_db import find_agent
        if find_agent(config, "config-agent") is None:
            config.agents.append({
                "name": "config-agent",
                "display_name": "Config Agent Display",
                "host": "",
                "qmd_index": "config-agent",
                "color": "#888",
                "registry_canonical_id": cid_config,
            })

        # Create a DIFFERENT registry identity with display_name matching the config agent's NAME
        rec_attacker = await registry.register(
            framework="test",
            display_name="config-agent",  # Matches config agent's NAME!
            origin="external-selfjoin",
            handle="@attacker-agent",
            user_id="attacker-user",
        )
        cid_attacker = rec_attacker["canonical_id"]
        await registry.set_status(cid_attacker, "active")
        await grants.add_grant(cid_attacker, "memory_write", project_id="prj-1")

        # Attacker tries to write using the config agent's name -> 403
        attacker_token = mint_registry_token(cid_attacker, priv, user_id="attacker-user", framework="test", project_id="prj-1")
        async with _make_client_with_token(app, attacker_token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": "config-agent"})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Agent can only write its own memory"

        # Legitimate config agent (matching canonical_id) can write -> 200
        legit_token = mint_registry_token(cid_config, priv, user_id="user-123", framework="test", project_id="prj-1")
        async with _make_client_with_token(app, legit_token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": "config-agent"})
            assert resp.status_code == 200
            assert resp.json()["stored"] is True

    async def test_registry_jwt_config_agent_without_canonical_id_gets_403(self, client_with_qmd):
        """Registry JWT where config agent has NO registry_canonical_id -> 403 (fail closed); matching identity with canonical_id gets 200."""
        _stub_http(client_with_qmd, {"/ingest": {"status": "ok"}})

        app = client_with_qmd._transport.app
        await self._init_stores(app)

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair
        config = app.state.config

        # Register a deployed config agent WITH registry_canonical_id
        rec_config = await registry.register(
            framework="test",
            display_name="Config Agent With CID",
            origin="taos-deployed",
            handle="@config-agent-cid",
            user_id="user-123",
        )
        cid_config = rec_config["canonical_id"]
        await grants.add_grant(cid_config, "memory_write", project_id="prj-1")

        # Add the agent to config WITH registry_canonical_id
        from tinyagentos.agent_db import find_agent
        if find_agent(config, "config-agent-cid") is None:
            config.agents.append({
                "name": "config-agent-cid",
                "display_name": "Config Agent With CID",
                "host": "",
                "qmd_index": "config-agent-cid",
                "color": "#888",
                "registry_canonical_id": cid_config,
            })

        # Register ANOTHER config agent WITHOUT registry_canonical_id (same display_name)
        rec_no_cid = await registry.register(
            framework="test",
            display_name="Config Agent No CID",
            origin="taos-deployed",
            handle="@config-agent-no-cid",
            user_id="user-456",
        )
        cid_no_cid = rec_no_cid["canonical_id"]
        await grants.add_grant(cid_no_cid, "memory_write", project_id="prj-1")

        # Add another config agent with same display_name but NO registry_canonical_id
        if find_agent(config, "config-agent-no-cid") is None:
            config.agents.append({
                "name": "config-agent-no-cid",
                "display_name": "Config Agent No CID",
                "host": "",
                "qmd_index": "config-agent-no-cid",
                "color": "#999",
                # NO registry_canonical_id key
            })

        # Attacker: registry identity with display_name matching the NO-CID config agent's NAME
        rec_attacker = await registry.register(
            framework="test",
            display_name="config-agent-no-cid",  # Matches config agent's NAME (the one without CID)
            origin="external-selfjoin",
            handle="@attacker-agent",
            user_id="attacker-user",
        )
        cid_attacker = rec_attacker["canonical_id"]
        await registry.set_status(cid_attacker, "active")
        await grants.add_grant(cid_attacker, "memory_write", project_id="prj-1")

        # Attacker tries to write using the no-CID config agent's name -> 403 (fail closed)
        attacker_token = mint_registry_token(cid_attacker, priv, user_id="attacker-user", framework="test", project_id="prj-1")
        async with _make_client_with_token(app, attacker_token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": "config-agent-no-cid"})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "Agent can only write its own memory"

        # Legitimate config agent WITH canonical_id can still write -> 200
        legit_token = mint_registry_token(cid_config, priv, user_id="user-123", framework="test", project_id="prj-1")
        async with _make_client_with_token(app, legit_token) as client:
            resp = await client.post("/api/memory/remember", json={"text": "test memory", "agent": "config-agent-cid"})
            assert resp.status_code == 200
            assert resp.json()["stored"] is True