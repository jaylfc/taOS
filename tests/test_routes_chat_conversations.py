"""Route tests for agent conversation sessions (#2123).

A conversation session with an agent is a DM channel between the user
and that agent. These tests pin the list/create contract the desktop
session selector relies on: sessions are per (user, agent), listed
most recent first, titled from the first user message, stamped with
the agent's model at creation, and starting a new chat never discards
the previous conversation.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

AGENT = "test-agent"


async def _make_dm(client, name="test-agent"):
    resp = await client.post("/api/chat/channels", json={
        "name": name, "type": "dm", "members": ["user", AGENT],
    })
    assert resp.status_code == 200
    return resp.json()


async def _post_user_message(client, channel_id, content):
    resp = await client.post("/api/chat/messages", json={
        "channel_id": channel_id, "author_id": "user",
        "author_type": "user", "content": content,
    })
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_list_conversations_empty_for_fresh_agent(client):
    resp = await client.get(f"/api/chat/agents/{AGENT}/conversations")
    assert resp.status_code == 200
    assert resp.json()["conversations"] == []


@pytest.mark.asyncio
async def test_create_conversation_makes_dm_channel(client):
    resp = await client.post(f"/api/chat/agents/{AGENT}/conversations")
    assert resp.status_code == 200
    conv = resp.json()["conversation"]
    assert conv["agent"] == AGENT
    assert conv["title"]  # falls back to a non-empty placeholder
    # The created session is a real DM channel the chat surface can open.
    ch = await client.get(f"/api/chat/channels/{conv['id']}")
    assert ch.status_code == 200
    assert ch.json()["type"] == "dm"
    assert AGENT in ch.json()["members"]


@pytest.mark.asyncio
async def test_create_stamps_agent_model(client, app):
    app.state.config.agents[0]["model"] = "test-model-9"
    resp = await client.post(f"/api/chat/agents/{AGENT}/conversations")
    assert resp.status_code == 200
    assert resp.json()["conversation"]["model"] == "test-model-9"


@pytest.mark.asyncio
async def test_new_chat_preserves_previous_conversation(client):
    first = (await client.post(
        f"/api/chat/agents/{AGENT}/conversations")).json()["conversation"]
    await _post_user_message(client, first["id"], "hello from the first chat")
    second = (await client.post(
        f"/api/chat/agents/{AGENT}/conversations")).json()["conversation"]
    assert second["id"] != first["id"]

    resp = await client.get(f"/api/chat/agents/{AGENT}/conversations")
    ids = [c["id"] for c in resp.json()["conversations"]]
    assert first["id"] in ids and second["id"] in ids
    # The previous conversation's history is still reachable.
    msgs = await client.get(f"/api/chat/channels/{first['id']}/messages")
    assert any(
        m["content"] == "hello from the first chat"
        for m in msgs.json()["messages"]
    )


@pytest.mark.asyncio
async def test_list_most_recent_first_and_title_from_first_user_message(client):
    older = await _make_dm(client)
    await _post_user_message(client, older["id"], "my very first question")
    newer = (await client.post(
        f"/api/chat/agents/{AGENT}/conversations")).json()["conversation"]

    resp = await client.get(f"/api/chat/agents/{AGENT}/conversations")
    convs = resp.json()["conversations"]
    assert convs[0]["id"] == newer["id"]
    by_id = {c["id"]: c for c in convs}
    assert by_id[older["id"]]["title"] == "my very first question"


@pytest.mark.asyncio
async def test_unknown_agent_404(client):
    assert (await client.get(
        "/api/chat/agents/nope/conversations")).status_code == 404
    assert (await client.post(
        "/api/chat/agents/nope/conversations")).status_code == 404


@pytest.mark.asyncio
async def test_group_channel_with_agent_is_not_a_conversation(client):
    resp = await client.post("/api/chat/channels", json={
        "name": "group", "type": "group",
        "members": ["user", AGENT, "other"],
    })
    assert resp.status_code == 200
    resp = await client.get(f"/api/chat/agents/{AGENT}/conversations")
    assert resp.json()["conversations"] == []


# ── Authorization gates (review round on #2123) ─────────────────────
# Sessions belong to a person: creating/listing requires a person's
# session, and a non-admin member must own the agent (registry
# attribution) or hold a share. test-agent here is a config-only agent
# with no registry row, so it is attributable to nobody and a member
# is refused on both routes; an agent bearer token is refused outright.

async def _invite_member(app) -> dict:
    """Invite + complete a non-admin member; returns the user record.
    Mirrors tests/test_global_routers_authz.py."""
    auth_mgr = app.state.auth
    existing = auth_mgr.find_user("member")
    if existing:
        return existing
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite(
        "member", invite_code, "Test Member", "", "memberauthzpass123")
    return auth_mgr.find_user("member")


@pytest_asyncio.fixture
async def member_client(app, client):
    """Cookie'd, CSRF-armed client for a non-admin member session.
    Depends on ``client`` so the admin the fixture creates exists."""
    from httpx import ASGITransport, AsyncClient
    from taos_test_csrf import csrf_event_hooks

    member = await _invite_member(app)
    token = app.state.auth.create_session(
        user_id=member["id"], long_lived=True)
    ac = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )
    yield ac
    await ac.aclose()


@pytest.mark.asyncio
async def test_member_cannot_list_or_create_sessions(member_client):
    resp = await member_client.get(f"/api/chat/agents/{AGENT}/conversations")
    assert resp.status_code == 403
    resp = await member_client.post(f"/api/chat/agents/{AGENT}/conversations")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_agent_token_cannot_manage_sessions(client, app):
    from httpx import ASGITransport, AsyncClient

    from tinyagentos.agent_registry_store import mint_registry_token

    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store in (registry, grants):
        if store._db is None:
            await store.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code", display_name="taOS dev",
        allow_reserved=True, origin="internal", handle="@conv-agent",
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    token = mint_registry_token(
        cid, priv, user_id="u", framework="claude-code", project_id=None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        resp = await ac.get(f"/api/chat/agents/{AGENT}/conversations")
        assert resp.status_code == 403
        resp = await ac.post(f"/api/chat/agents/{AGENT}/conversations")
        assert resp.status_code == 403
