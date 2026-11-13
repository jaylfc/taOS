"""Owner checks compare unique canonical ids, never a colliding slug or handle.

A config agent's name is a slug that any user can also mint as a registry
display name (slug) or handle. ``resolve_agent_owner`` must therefore
attribute a config agent through its ``registry_canonical_id`` only, and must
refuse to attribute a registry-only slug that more than one active row carries.
The route tests cover the three callers that used to resolve the name by handle
on their own (agent desktop, agent versions, agent GitHub grants).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.agent_registry_store import AgentRegistryStore
from tinyagentos.auth_context import resolve_agent_owner


@pytest_asyncio.fixture
async def registry(tmp_path):
    store = AgentRegistryStore(tmp_path / "registry.db")
    await store.init()
    try:
        yield store
    finally:
        await store.close()


def _request(registry, agents: list[dict]):
    config = SimpleNamespace(agents=agents)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_registry=registry, config=config)))


@pytest.mark.asyncio
class TestResolveAgentOwner:
    async def test_config_agent_is_attributed_by_canonical_id_not_handle(self, registry):
        # Bob mints a registry identity whose handle equals alice's config agent
        # name, and whose slug (the older row) matches it too.
        await registry.register(framework="claude-code", display_name="Alpha", handle="alpha", user_id="bob")
        alice = await registry.register(framework="claude-code", display_name="Alpha", user_id="alice")
        req = _request(registry, [{"name": "alpha", "registry_canonical_id": alice["canonical_id"]}])

        assert await resolve_agent_owner(req, "alpha") == "alice"

    async def test_config_agent_is_attributed_by_canonical_id_not_slug(self, registry):
        # Alice's config agent got the "-2" suffix at deploy time, so its name
        # is a slug her registry row does not carry. Bob registers that slug.
        alice = await registry.register(framework="claude-code", display_name="Alpha", user_id="alice")
        await registry.register(framework="claude-code", display_name="alpha 2", user_id="bob")
        req = _request(registry, [{"name": "alpha-2", "registry_canonical_id": alice["canonical_id"]}])

        assert await resolve_agent_owner(req, "alpha-2") == "alice"

    async def test_unattributed_config_agent_is_nobodys_even_with_a_colliding_row(self, registry):
        await registry.register(framework="claude-code", display_name="Alpha", user_id="bob")
        await registry.register(framework="claude-code", display_name="x", handle="beta", user_id="bob")
        req = _request(registry, [{"name": "alpha"}, {"name": "beta"}])

        assert await resolve_agent_owner(req, "alpha") is None
        assert await resolve_agent_owner(req, "beta") is None

    async def test_registry_only_slug_shared_by_two_rows_attributes_nobody(self, registry):
        await registry.register(framework="claude-code", display_name="Gamma", user_id="alice")
        await registry.register(framework="claude-code", display_name="Gamma", user_id="bob")
        req = _request(registry, [])

        assert await resolve_agent_owner(req, "gamma") is None

    async def test_registry_only_agent_resolves_by_unique_slug_handle_and_id(self, registry):
        rec = await registry.register(
            framework="claude-code", display_name="Delta", handle="delta-bot", user_id="alice"
        )
        req = _request(registry, [])

        assert await resolve_agent_owner(req, rec["canonical_id"]) == "alice"
        assert await resolve_agent_owner(req, "delta-bot") == "alice"
        assert await resolve_agent_owner(req, "delta") == "alice"

    async def test_suspended_or_revoked_config_agent_is_nobodys(self, registry):
        suspended = await registry.register(framework="claude-code", display_name="Eps", user_id="alice")
        revoked = await registry.register(framework="claude-code", display_name="Zeta", user_id="alice")
        await registry.set_status(suspended["canonical_id"], "suspended")
        await registry.revoke(revoked["canonical_id"])
        req = _request(registry, [
            {"name": "eps", "registry_canonical_id": suspended["canonical_id"]},
            {"name": "zeta", "registry_canonical_id": revoked["canonical_id"]},
        ])

        assert await resolve_agent_owner(req, "eps") is None
        assert await resolve_agent_owner(req, "zeta") is None

    async def test_slug_does_not_match_a_longer_slug_shaped_like_a_canonical_tail(self, registry):
        # Bob's display name slugifies to "theta-20260101-120000-x", so his
        # canonical id starts "theta-20260101-120000-": it must not count as
        # a row for the bare slug "theta".
        alice = await registry.register(framework="claude-code", display_name="Theta", user_id="alice")
        await registry.register(framework="claude-code", display_name="theta 20260101 120000 x", user_id="bob")
        req = _request(registry, [])

        assert await registry.count_by_slug("theta") == 1
        assert (await registry.get_by_slug("theta"))["canonical_id"] == alice["canonical_id"]
        assert await resolve_agent_owner(req, "theta") == "alice"


def _add_member_user(app, username: str, password: str) -> str:
    invite = app.state.auth.add_user_invite(username, "admin")
    app.state.auth.complete_invite(username, invite, username.title(), f"{username}@test.com", password)
    return app.state.auth.find_user(username)["id"]


@pytest_asyncio.fixture
async def colliding_clients(app):
    """``(alice, bob)`` member clients; the config agent ``test-agent`` belongs
    to alice by canonical id, and bob holds a registry row whose handle AND
    slug both read ``test-agent``."""
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    alice_uid = _add_member_user(app, "alice", "alicepass1")
    bob_uid = _add_member_user(app, "bob", "bobspass11")
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    secrets_store = app.state.secrets
    if secrets_store._db is None:
        await secrets_store.init()
    alice_row = await registry.register(framework="claude-code", display_name="Mine", user_id=alice_uid)
    await registry.register(
        framework="claude-code", display_name="test agent", handle="test-agent", user_id=bob_uid
    )
    app.state.config.agents[0]["registry_canonical_id"] = alice_row["canonical_id"]
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    tokens = [app.state.auth.create_session(user_id=u, long_lived=True) for u in (alice_uid, bob_uid)]
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": tokens[0]}, event_hooks=csrf_event_hooks(),
    ) as alice_c, AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": tokens[1]}, event_hooks=csrf_event_hooks(),
    ) as bob_c:
        yield alice_c, bob_c
    await registry.close()
    await secrets_store.close()


@pytest.mark.asyncio
class TestRoutesUseCanonicalOwner:
    async def test_desktop_status_refuses_the_handle_collider(self, colliding_clients):
        alice, bob = colliding_clients
        assert (await alice.get("/api/agents/test-agent/desktop/status")).status_code == 200
        assert (await bob.get("/api/agents/test-agent/desktop/status")).status_code == 403

    async def test_versions_refuses_the_handle_collider(self, colliding_clients):
        _alice, bob = colliding_clients
        assert (await bob.get("/api/agents/test-agent/versions")).status_code == 403

    async def test_version_diff_and_revert_refuse_the_handle_collider(self, colliding_clients):
        _alice, bob = colliding_clients
        assert (await bob.get("/api/agents/test-agent/versions/abc12345/diff")).status_code == 403
        assert (await bob.post("/api/agents/test-agent/versions/abc12345/revert")).status_code == 403

    async def test_github_grants_refuse_the_handle_collider(self, colliding_clients):
        alice, bob = colliding_clients
        assert (await alice.get("/api/secrets/agent/test-agent/github")).status_code == 200
        assert (await bob.get("/api/secrets/agent/test-agent/github")).status_code == 403

    async def test_github_grants_of_an_unattributed_agent_are_admin_only(self, colliding_clients, app):
        _alice, bob = colliding_clients
        app.state.config.agents[0].pop("registry_canonical_id")
        assert (await bob.get("/api/secrets/agent/test-agent/github")).status_code == 403
