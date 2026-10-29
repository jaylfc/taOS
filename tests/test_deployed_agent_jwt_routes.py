from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import patch

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.app import create_app
from tinyagentos.trace_store import TraceStoreRegistry
from taos_test_csrf import csrf_event_hooks


@pytest_asyncio.fixture
async def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [], "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {"name": "alpha", "registry_canonical_id": "PLACEHOLDER", "model": "gpt-4"},
            {"name": "beta", "registry_canonical_id": "PLACEHOLDER2", "model": "gpt-4"},
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (data / ".setup_complete").touch()
    app = create_app(data_dir=data)
    admin = app.state.auth
    if not admin.is_configured():
        admin.setup_user("admin", "Admin", "", "adminpass")
    owner = admin.find_user("admin")["id"]

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    from tinyagentos.skills import SkillStore
    skills = SkillStore(tmp_path / "skills.db")
    await skills.init()
    app.state.skills = skills
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")
    app.state.agent_registry = registry
    app.state.agent_grants = grants
    app.state.agent_registry_keypair = (priv, pub)
    app.state.trace_registry = TraceStoreRegistry(tmp_path / "tr")

    rec = await app.state.agent_registry.register(framework="hermes", display_name="alpha", user_id=owner)
    rec_beta = await app.state.agent_registry.register(framework="hermes", display_name="beta", user_id=owner)

    cfg = app.state.config
    for a in getattr(cfg, "agents", []):
        if a.get("name") == "alpha":
            a["registry_canonical_id"] = rec["canonical_id"]
        if a.get("name") == "beta":
            a["registry_canonical_id"] = rec_beta["canonical_id"]
    try:
        from tinyagentos.config import save_config_locked
        await save_config_locked(cfg, cfg.config_path)
    except Exception:
        pass

    token = mint_registry_token(rec["canonical_id"], priv, user_id=owner)
    rec_ext = await app.state.agent_registry.register(framework="hermes", display_name="gamma", user_id=owner, origin="external-selfjoin")
    token_ext = mint_registry_token(rec_ext["canonical_id"], priv, user_id=owner)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", event_hooks=csrf_event_hooks()) as client:
        from tinyagentos.chat.typing_registry import TypingRegistry
        app.state.typing = TypingRegistry()
        client.tokens = {"alpha": token, "ext": token_ext}
        client.owner = owner
        client.registry = registry
        client.app = app
        yield client
    await registry.close()
    await grants.close()


def _auth(client, who):
    return {"Authorization": f"Bearer {client.tokens[who]}"}


@pytest.mark.asyncio
async def test_chat_thinking_slug_match(env):
    resp = await env.post("/api/chat/channels/c1/thinking", headers=_auth(env, "alpha"), json={"slug": "alpha", "state": "start"})
    assert resp.status_code == 200
    resp2 = await env.post("/api/chat/channels/c1/thinking", headers=_auth(env, "alpha"), json={"slug": "beta", "state": "start"})
    assert resp2.status_code == 403


@pytest.mark.asyncio
async def test_openclaw_events_fenced(env):
    resp = await env.get("/api/openclaw/sessions/beta/events", headers=_auth(env, "alpha"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_skill_exec_fenced_by_state_agent_name(env):
    resp = await env.get("/api/skill-exec/tools?agent_name=beta", headers=_auth(env, "alpha"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_skill_exec_matching_agent_name_allowed(env):
    resp = await env.get("/api/skill-exec/tools?agent_name=alpha", headers=_auth(env, "alpha"))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_off_allowlist_admin_routes_no_access(env):
    resp = await env.get("/api/config", headers=_auth(env, "alpha"))
    assert resp.status_code in (401, 403)
    resp = await env.get("/api/agents", headers=_auth(env, "alpha"))
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_external_selfjoin_no_deployed_via(env):
    resp = await env.post("/api/chat/channels/c1/thinking", headers=_auth(env, "ext"), json={"slug": "alpha", "state": "start"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_trace_forged_bearer_refused(env):
    resp = await env.post(
        "/api/trace",
        headers={"Authorization": "Bearer not-a-jwt-at-all"},
        json={"agent_name": "beta", "kind": "message_out"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_trace_external_selfjoin_refused(env):
    resp = await env.post(
        "/api/trace",
        headers=_auth(env, "ext"),
        json={"agent_name": "beta", "kind": "message_out"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_trace_deployed_agent_binds_own_name(env):
    resp = await env.post(
        "/api/trace",
        headers=_auth(env, "alpha"),
        json={"agent_name": "beta", "kind": "message_out"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_name"] == "alpha"


@pytest.mark.asyncio
async def test_deployed_jwt_on_desktop_not_deployed_principal(env):
    resp = await env.post(
        "/api/desktop/command",
        headers=_auth(env, "alpha"),
        json={"kind": "open-app", "payload": {}},
    )
    assert resp.status_code in (401, 403)
    assert resp.status_code != 200


@pytest.mark.asyncio
async def test_openclaw_events_forged_bearer_refused(env):
    resp = await env.get(
        "/api/openclaw/sessions/alpha/events",
        headers={"Authorization": "Bearer not-a-jwt-at-all"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_deployed_agent_skill_assignment_required(env):
    """Deployed agent must be assigned a skill to execute it (major security fix)."""
    # Find a skill that exists in SKILL_IMPLEMENTATIONS but is not assigned to alpha
    skills = env.app.state.skills
    all_skills = await skills.list_skills()
    # Pick a skill that has an implementation (e.g., file_read)
    skill_id = "file_read"
    skill = await skills.get_skill(skill_id)
    assert skill is not None
    assert any(s["id"] == skill_id for s in all_skills)
    
    # Verify alpha does NOT have this skill assigned
    assigned = await skills.get_agent_skills("alpha")
    assigned_ids = {s["id"] for s in assigned}
    assert skill_id not in assigned_ids, f"Test setup failed: {skill_id} should not be assigned to alpha"
    
    # Track if the skill implementation is called
    called = {"count": 0}
    
    # Patch the specific skill implementation narrowly
    from tinyagentos.routes.skill_exec import SKILL_IMPLEMENTATIONS
    original_impl = SKILL_IMPLEMENTATIONS[skill_id]
    
    async def tracking_impl(args, request):
        called["count"] += 1
        return await original_impl(args, request)
    
    with patch.dict(SKILL_IMPLEMENTATIONS, {skill_id: tracking_impl}):
        # Deployed agent alpha tries to call a skill NOT assigned to it
        resp = await env.post(
            f"/api/skill-exec/{skill_id}/call",
            headers=_auth(env, "alpha"),
            json={"args": {}},
        )
        # Should be 403 forbidden
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        assert "forbidden: skill not assigned to agent" in resp.text
        # Skill implementation should NOT have been called
        assert called["count"] == 0, "Skill implementation was called despite missing assignment"
    
    # Now assign the skill to alpha
    await skills.assign_skill("alpha", skill_id, None)
    
    # Same call should now work (not 403 for assignment reason)
    # Note: it might still fail for other reasons (e.g., governance), but not 403 for assignment
    called["count"] = 0
    with patch.dict(SKILL_IMPLEMENTATIONS, {skill_id: tracking_impl}):
        resp = await env.post(
            f"/api/skill-exec/{skill_id}/call",
            headers=_auth(env, "alpha"),
            json={"args": {}},
        )
        assert resp.status_code != 403 or "forbidden: skill not assigned to agent" not in resp.text, \
            f"Expected not 403 for assignment, got {resp.status_code}: {resp.text}"
        # Skill implementation should now be called (or at least reach the governance check)
        # The exact behavior depends on governance, but it should pass the assignment check


@pytest.mark.asyncio
async def test_unknown_route_with_device_bearer_and_session_returns_404(env):
    """Valid taos_session + device Bearer on unknown route returns 404, not 401 (minor fix)."""
    from tinyagentos.device_store import DEVICE_TOKEN_PREFIX
    
    # First, login as admin to get a valid taos_session cookie
    admin_client = AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://test",
        event_hooks=csrf_event_hooks(),
    )
    login_resp = await admin_client.post("/auth/login", json={"username": "admin", "password": "adminpass"})
    assert login_resp.status_code == 200
    session_cookie = admin_client.cookies.get("taos_session")
    assert session_cookie, "Failed to get taos_session cookie"
    
    # Now make a request to an unknown route with:
    # - Valid taos_session cookie
    # - Device Bearer token (invalid, but with correct prefix)
    headers = {"Authorization": f"Bearer {DEVICE_TOKEN_PREFIX}junk"}
    cookies = {"taos_session": session_cookie}
    
    resp = await admin_client.get(
        "/api/no-such-route-xyz",
        headers=headers,
        cookies=cookies,
    )
    
    # Should return 404 (route not found), NOT 401 (unauthorized)
    # The session cookie is valid, so the auth should pass, then route matching fails -> 404
    assert resp.status_code == 404, f"Expected 404 for unknown route with valid session, got {resp.status_code}: {resp.text}"
    
    await admin_client.aclose()


    async def test_deployed_agent_runtime_error_returns_500(env):
        """(a) A route handler that raises RuntimeError reached with alpha's
        deployed JWT returns 500 (not 401) and the handler ran exactly once."""
        skills = env.app.state.skills
        await skills.assign_skill("alpha", "file_read", None)
        
        from tinyagentos.routes.skill_exec import SKILL_IMPLEMENTATIONS
        called = {"count": 0}
        
        async def runtime_error_impl(args, request):
            called["count"] += 1
            raise RuntimeError("deliberate error")
        
        with patch.dict(SKILL_IMPLEMENTATIONS, {"file_read": runtime_error_impl}):
            resp = await env.post(
                "/api/skill-exec/file_read/call",
                headers=_auth(env, "alpha"),
                json={"args": {}},
            )
            assert resp.status_code == 500, f"Expected 500, got {resp.status_code}: {resp.text}"
            assert called["count"] == 1, f"Expected handler called exactly once, got {called['count']}"
    
    async def test_deployed_agent_thinking_channel_membership(env):
        """(b) alpha's JWT posting thinking to a channel alpha is not a member
        of gets 403; a member channel gets 200."""
        ch_store = env.app.state.chat_channels
        
        await ch_store.add("c_not_member", members=[], creator="system")
        
        resp = await env.post(
            "/api/chat/channels/c_not_member/thinking",
            headers=_auth(env, "alpha"),
            json={"slug": "alpha", "state": "start"},
        )
        assert resp.status_code == 403, f"Expected 403 for non-member channel, got {resp.status_code}: {resp.text}"
        
        await ch_store.add("c_member", members=["alpha"], creator="system")
        
        resp = await env.post(
            "/api/chat/channels/c_member/thinking",
            headers=_auth(env, "alpha"),
            json={"slug": "alpha", "state": "start"},
        )
        assert resp.status_code == 200, f"Expected 200 for member channel, got {resp.status_code}: {resp.text}"

