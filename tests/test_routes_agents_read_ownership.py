"""Per-agent reads belong to the agent's owner, and no read returns llm_key.

llm_key is the agent's bearer credential (``_resolve_agent_by_bearer``), so a
response that carries it lets the reader act as the agent.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

ALPHA_KEY = "sk-agent-ALPHA-SECRET-7731"
GHOST_KEY = "sk-agent-GHOST-SECRET-4410"


def _add_member(app, username: str, password: str) -> str:
    invite = app.state.auth.add_user_invite(username, "admin")
    app.state.auth.complete_invite(username, invite, username.title(), f"{username}@test.com", password)
    return app.state.auth.find_user(username)["id"]


def _add_agent(app, name: str, **extra) -> dict:
    agent = {"name": name, "host": "127.0.0.1", "qmd_index": "test", "color": "#cccccc", **extra}
    app.state.config.agents.append(agent)
    return agent


@pytest_asyncio.fixture
async def owner_clients(app, client):
    """``(alice, bob)`` member clients; agent ``alpha`` (with an llm_key) is alice's."""
    alice_uid = _add_member(app, "alice", "alicepass1")
    bob_uid = _add_member(app, "bob", "bobspass11")
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    row = await registry.register(framework="claude-code", display_name="alpha", user_id=alice_uid)
    _add_agent(app, "alpha", llm_key=ALPHA_KEY, registry_canonical_id=row["canonical_id"])
    app.state.config.archived_agents.append(
        {"id": "ghost-1", "archived_slug": "ghost",
         "original": {"name": "ghost", "llm_key": GHOST_KEY}}
    )

    clients = []
    for uid in (alice_uid, bob_uid):
        token = app.state.auth.create_session(user_id=uid, long_lived=True)
        clients.append(AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            cookies={"taos_session": token}, event_hooks=csrf_event_hooks(),
        ))
    try:
        yield clients[0], clients[1]
    finally:
        for c in clients:
            await c.aclose()
        await registry.close()


PER_AGENT_READS = [
    "/api/agents/alpha",
    "/api/agents/alpha/export",
    "/api/agents/alpha/logs",
    "/api/agents/alpha/budget",
    "/api/agents/alpha/deploy-status",
]


class TestAgentReadOwnership:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PER_AGENT_READS)
    async def test_other_member_is_refused(self, owner_clients, path):
        _alice, bob = owner_clients
        resp = await bob.get(path)
        assert resp.status_code == 403, (path, resp.status_code, resp.text)
        assert ALPHA_KEY not in resp.text

    @pytest.mark.asyncio
    async def test_owner_can_read_and_export(self, owner_clients):
        alice, _bob = owner_clients
        assert (await alice.get("/api/agents/alpha")).status_code == 200
        resp = await alice.get("/api/agents/alpha/export")
        assert resp.status_code == 200
        assert resp.json()["agent"]["name"] == "alpha"
        assert (await alice.get("/api/agents/alpha/budget")).status_code == 200

    @pytest.mark.asyncio
    async def test_member_list_shows_only_own_agents(self, owner_clients, app):
        _add_agent(app, "orphan")  # config-only, no registry row: nobody's to list
        alice, bob = owner_clients
        assert [a["name"] for a in (await alice.get("/api/agents")).json()] == ["alpha"]
        assert (await bob.get("/api/agents")).json() == []
        assert (await bob.get("/api/agents/archived")).json() == []

    @pytest.mark.asyncio
    async def test_admin_still_sees_every_agent(self, owner_clients, client, app):
        _add_agent(app, "orphan")
        names = {a["name"] for a in (await client.get("/api/agents")).json()}
        assert {"alpha", "orphan"} <= names
        assert (await client.get("/api/agents/alpha/export")).status_code == 200


class TestNoReadReturnsLlmKey:
    """Not even to the owner or an admin: import re-mints the key, so it has no use here."""

    @pytest.mark.asyncio
    async def test_owner_reads_carry_no_llm_key(self, owner_clients):
        alice, _bob = owner_clients
        for path in ("/api/agents", "/api/agents/alpha", "/api/agents/alpha/export"):
            resp = await alice.get(path)
            assert resp.status_code == 200, path
            assert ALPHA_KEY not in resp.text, path
            assert "llm_key" not in resp.text, path

    @pytest.mark.asyncio
    async def test_admin_reads_carry_no_llm_key(self, owner_clients, client):
        for path in (
            "/api/agents", "/api/agents/alpha", "/api/agents/alpha/export", "/api/agents/archived",
        ):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert ALPHA_KEY not in resp.text and GHOST_KEY not in resp.text, path
            assert "llm_key" not in resp.text, path

    @pytest.mark.asyncio
    async def test_stripping_does_not_touch_stored_config(self, owner_clients, client, app):
        await client.get("/api/agents/alpha/export")
        await client.get("/api/agents")
        stored = next(a for a in app.state.config.agents if a["name"] == "alpha")
        assert stored["llm_key"] == ALPHA_KEY
