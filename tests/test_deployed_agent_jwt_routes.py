from __future__ import annotations

import asyncio
import os
import time

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
