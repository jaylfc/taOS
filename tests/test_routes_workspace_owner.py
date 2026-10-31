"""Ownership on the per-agent messages and usage routes (security sweep).

An agent's messages, contacts and LLM usage belong to the agent's owner. A
per-agent local token maps to the admin user, so it is fenced to its own
agent for reads and to its own name as sender.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks


def _add_agent(app, name: str) -> None:
    app.state.config.agents.append({
        "name": name,
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#cccccc",
    })


def _add_member(app, username: str, password: str) -> str:
    invite = app.state.auth.add_user_invite(username, "admin")
    app.state.auth.complete_invite(username, invite, username.title(), f"{username}@test.com", password)
    return app.state.auth.find_user(username)["id"]


@pytest_asyncio.fixture
async def owner_clients(app, client):
    """``(alice, bob)`` member clients; agent ``alpha`` belongs to alice.

    ``client`` is requested first so the shared app fixture has its admin and
    stores set up. alice's registry row carries the canonical id the config
    agent points at, so the owner check attributes ``alpha`` to her.
    """
    alice_uid = _add_member(app, "alice", "alicepass1")
    bob_uid = _add_member(app, "bob", "bobspass11")
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    row = await registry.register(framework="claude-code", display_name="alpha", user_id=alice_uid)
    _add_agent(app, "alpha")
    app.state.config.agents[-1]["registry_canonical_id"] = row["canonical_id"]
    await app.state.agent_messages.send(from_agent="gamma", to_agent="alpha", message="alice only")

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


def _bearer(app, agent_name: str) -> AsyncClient:
    token = app.state.auth.mint_agent_local_token(agent_name)
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


class TestWorkspaceMessagesOwnership:

    @pytest.mark.asyncio
    async def test_owner_can_read(self, owner_clients):
        alice, _bob = owner_clients
        resp = await alice.get("/api/agents/alpha/messages")
        assert resp.status_code == 200
        assert any(m["message"] == "alice only" for m in resp.json())
        assert (await alice.get("/api/agents/alpha/workspace/messages/contacts")).status_code == 200
        assert (await alice.get("/api/agents/alpha/workspace/usage")).status_code == 200

    @pytest.mark.asyncio
    async def test_admin_can_read(self, owner_clients, client):
        assert (await client.get("/api/agents/alpha/messages")).status_code == 200

    @pytest.mark.asyncio
    async def test_other_member_cannot_read(self, owner_clients):
        _alice, bob = owner_clients
        assert (await bob.get("/api/agents/alpha/messages")).status_code == 403
        assert (await bob.get("/api/agents/alpha/workspace/messages/contacts")).status_code == 403
        assert (await bob.get("/api/agents/alpha/workspace/usage")).status_code == 403
        assert (await bob.get("/api/agents/alpha/files")).status_code == 403

    @pytest.mark.asyncio
    async def test_other_member_cannot_send(self, owner_clients, app):
        _alice, bob = owner_clients
        resp = await bob.post("/api/agents/alpha/messages", json={"from_agent": "user", "message": "planted"})
        assert resp.status_code == 403
        msgs = await app.state.agent_messages.get_messages("alpha")
        assert not any(m["message"] == "planted" for m in msgs)

    @pytest.mark.asyncio
    async def test_owner_can_send(self, owner_clients):
        alice, _bob = owner_clients
        resp = await alice.post("/api/agents/alpha/messages", json={"from_agent": "user", "message": "hi"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_agent_token_reads_only_its_own_agent(self, owner_clients, app):
        _add_agent(app, "beta")
        async with _bearer(app, "beta") as beta:
            assert (await beta.get("/api/agents/beta/messages")).status_code == 200
            assert (await beta.get("/api/agents/alpha/messages")).status_code == 403
            assert (await beta.get("/api/agents/alpha/workspace/messages/contacts")).status_code == 403
            assert (await beta.get("/api/agents/alpha/workspace/usage")).status_code == 403
            assert (await beta.get("/api/agents/alpha/files")).status_code == 403

    @pytest.mark.asyncio
    async def test_agent_token_cannot_spoof_sender(self, owner_clients, app):
        _add_agent(app, "beta")
        async with _bearer(app, "beta") as beta:
            resp = await beta.post("/api/agents/alpha/messages", json={"from_agent": "gamma", "message": "spoof"})
            assert resp.status_code == 403
            resp = await beta.post("/api/agents/alpha/messages", json={"from_agent": "beta", "message": "hello"})
            assert resp.status_code == 200
        msgs = await app.state.agent_messages.get_messages("alpha")
        assert not any(m["message"] == "spoof" for m in msgs)
