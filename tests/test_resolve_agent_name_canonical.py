"""A registry identity maps to a config agent by canonical id, never by name.

``routes.delegation._resolve_agent_name`` turns a registry canonical id into
the config agent whose policy grants, task assignments and memory index the
caller then uses. Matching the registry row's display name or handle against
config agent names let any identity whose name collides with someone else's
config agent act as that agent (and read its taOSmd memory).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import AgentRegistryStore, mint_registry_token
from tinyagentos.routes.delegation import _resolve_agent_name


@pytest_asyncio.fixture
async def registry(tmp_path):
    store = AgentRegistryStore(tmp_path / "registry.db")
    await store.init()
    try:
        yield store
    finally:
        await store.close()


def _request(registry, agents):
    state = SimpleNamespace(agent_registry=registry, config=SimpleNamespace(agents=agents))
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
class TestResolveAgentName:
    async def test_owner_identity_maps_by_canonical_id(self, registry):
        alice = await registry.register(framework="hermes", display_name="Alpha Bot", user_id="alice")
        req = _request(registry, [{"name": "alpha", "registry_canonical_id": alice["canonical_id"]}])
        assert await _resolve_agent_name(req, alice["canonical_id"]) == "alpha"

    async def test_colliding_display_name_does_not_map(self, registry):
        alice = await registry.register(framework="hermes", display_name="Alpha Bot", user_id="alice")
        bob = await registry.register(
            framework="grok", display_name="alpha", origin="external-selfjoin", user_id="bob"
        )
        req = _request(registry, [{"name": "alpha", "registry_canonical_id": alice["canonical_id"]}])
        assert await _resolve_agent_name(req, bob["canonical_id"]) is None

    async def test_colliding_handle_does_not_map(self, registry):
        bob = await registry.register(framework="grok", display_name="x", handle="@alpha", user_id="bob")
        req = _request(registry, [{"name": "alpha"}])
        assert await _resolve_agent_name(req, bob["canonical_id"]) is None

    async def test_config_name_still_resolves_to_itself(self, registry):
        req = _request(registry, [{"name": "alpha"}])
        assert await _resolve_agent_name(req, "alpha") == "alpha"


class _CapturingHttp:
    def __init__(self):
        self.params = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.params.append(dict(params or {}))

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"chunks": []}

        return _Resp()


@pytest.mark.asyncio
async def test_registry_jwt_with_colliding_name_cannot_read_another_agents_memory(app):
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    app.state._startup_complete = True
    registry, grants = app.state.agent_registry, app.state.agent_grants

    owner = await registry.register(framework="hermes", display_name="Owner", user_id="alice")
    app.state.config.agents[0]["registry_canonical_id"] = owner["canonical_id"]
    victim = app.state.config.agents[0]["name"]

    intruder = await registry.register(
        framework="grok", display_name=victim, origin="external-selfjoin", handle="@intruder"
    )
    cid = intruder["canonical_id"]
    await registry.set_status(cid, "active")
    await grants.add_grant(cid, "memory_read", project_id="prj-1")
    priv, _pub = app.state.agent_registry_keypair
    token = mint_registry_token(cid, priv, user_id="bob", framework="grok", project_id="prj-1")

    http = _CapturingHttp()
    app.state.http_client = http
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            resp = await client.get("/api/memory/browse")
        assert resp.status_code == 200
        assert http.params, "browse must have queried the memory index"
        db_path = http.params[-1]["dbPath"]
        assert f"/{victim}/" not in db_path, f"intruder read {victim}'s index: {db_path}"
    finally:
        await registry.close()
        await grants.close()
