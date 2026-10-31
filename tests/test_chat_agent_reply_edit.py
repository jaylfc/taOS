"""Editing the last completed agent reply in place (#834).

The owner may rewrite the most recent agent message of a thread; the stored
content changes, so the next turn's context window carries the edited text.
Any other agent message (an older one, or one still streaming) is refused
with 409.
"""
import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from taos_test_csrf import csrf_event_hooks

from tinyagentos.chat.context_window import build_context_window


def _make_app(tmp_path):
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


def _client(app, user_id):
    token = app.state.auth.create_session(user_id=user_id, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


async def _setup(tmp_path):
    app = _make_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user("admin", "Admin Name", "", "testpass")
    rec = app.state.auth.find_user("admin")
    ch = await app.state.chat_channels.create_channel(
        name="tom", type="dm", members=["user", "tom"], created_by="user",
    )
    return app, rec, ch["id"]


@pytest.mark.asyncio
async def test_edit_last_agent_reply_becomes_next_turn_context(tmp_path):
    app, rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    await store.send_message(
        channel_id=ch_id, author_id=rec["id"], author_type="user", content="hi",
    )
    reply = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent",
        content="original agent text",
    )
    async with _client(app, rec["id"]) as client:
        r = await client.patch(
            f"/api/chat/messages/{reply['id']}/agent-reply",
            json={"content": "edited agent text"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "edited agent text"
    assert r.json()["edited_at"] is not None

    recent = await store.get_messages(channel_id=ch_id, limit=30)
    context = build_context_window(recent, limit=20, max_tokens=4000)
    contents = [m["content"] for m in context]
    assert "edited agent text" in contents
    assert "original agent text" not in contents
    # The edit rewrites in place: nothing after it is truncated or re-run.
    assert contents == ["hi", "edited agent text"]


@pytest.mark.asyncio
async def test_edit_older_agent_reply_returns_409(tmp_path):
    app, rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    older = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="first",
    )
    await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="second",
    )
    async with _client(app, rec["id"]) as client:
        r = await client.patch(
            f"/api/chat/messages/{older['id']}/agent-reply",
            json={"content": "rewrite"},
        )
    assert r.status_code == 409, r.text
    assert (await store.get_message(older["id"]))["content"] == "first"


@pytest.mark.asyncio
async def test_edit_streaming_agent_reply_returns_409(tmp_path):
    app, rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    live = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent",
        content="partial", state="streaming",
    )
    async with _client(app, rec["id"]) as client:
        r = await client.patch(
            f"/api/chat/messages/{live['id']}/agent-reply",
            json={"content": "rewrite"},
        )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_edit_agent_reply_with_content_blocks_returns_409(tmp_path):
    app, rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    rich = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="see image",
        content_blocks=[{"kind": "status", "text": "done"}],
    )
    async with _client(app, rec["id"]) as client:
        r = await client.patch(
            f"/api/chat/messages/{rich['id']}/agent-reply",
            json={"content": "rewrite"},
        )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_edit_agent_reply_scoped_to_its_thread(tmp_path):
    """A newer agent reply in a different thread does not make the
    top-level reply stale: "last" is per thread."""
    app, rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    top = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="top",
    )
    await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent",
        content="in thread", thread_id=top["id"],
    )
    async with _client(app, rec["id"]) as client:
        r = await client.patch(
            f"/api/chat/messages/{top['id']}/agent-reply",
            json={"content": "top edited"},
        )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_edit_user_message_via_agent_reply_route_returns_409(tmp_path):
    app, rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    mine = await store.send_message(
        channel_id=ch_id, author_id=rec["id"], author_type="user", content="mine",
    )
    async with _client(app, rec["id"]) as client:
        r = await client.patch(
            f"/api/chat/messages/{mine['id']}/agent-reply",
            json={"content": "rewrite"},
        )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_edit_agent_reply_non_owner_returns_403(tmp_path):
    app, rec, ch_id = await _setup(tmp_path)
    auth = app.state.auth
    code = auth.add_user_invite("bob", invited_by_username="admin")
    auth.complete_invite(
        username="bob", invite_code=code, full_name="Bob",
        email="bob@test.local", password="bobspass11",
    )
    bob_id = auth.find_user("bob")["id"]
    store = app.state.chat_messages
    reply = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="tom's",
    )
    async with _client(app, bob_id) as client:
        r = await client.patch(
            f"/api/chat/messages/{reply['id']}/agent-reply",
            json={"content": "hacked"},
        )
    assert r.status_code == 403, r.text
    assert (await store.get_message(reply["id"]))["content"] == "tom's"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["[]", "null", '"text"'])
async def test_edit_agent_reply_non_object_body_returns_400(tmp_path, body):
    app, rec, ch_id = await _setup(tmp_path)
    reply = await app.state.chat_messages.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="x",
    )
    async with _client(app, rec["id"]) as client:
        r = await client.patch(
            f"/api/chat/messages/{reply['id']}/agent-reply",
            content=body, headers={"content-type": "application/json"},
        )
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_store_refuses_stale_reply_in_the_write_itself(tmp_path):
    """The recency check lives in the UPDATE: once a newer agent reply has
    landed, the store refuses the stale id and writes nothing."""
    app, _rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    older = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="first",
    )
    assert await store.edit_last_agent_reply(older["id"], "ok") is True
    await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="second",
    )
    assert await store.edit_last_agent_reply(older["id"], "late") is False
    assert (await store.get_message(older["id"]))["content"] == "ok"


@pytest.mark.asyncio
async def test_bound_agent_token_cannot_edit_another_agents_reply(tmp_path):
    """A per-agent local token maps to the admin user, so the owner check
    alone would pass: the bound agent must also be the reply's author."""
    app, _rec, ch_id = await _setup(tmp_path)
    store = app.state.chat_messages
    reply = await store.send_message(
        channel_id=ch_id, author_id="tom", author_type="agent", content="tom's",
    )
    token = app.state.auth.mint_agent_local_token("other")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
        event_hooks=csrf_event_hooks(),
    ) as client:
        r = await client.patch(
            f"/api/chat/messages/{reply['id']}/agent-reply",
            json={"content": "hijacked"},
        )
    assert r.status_code == 403, r.text
    assert (await store.get_message(reply["id"]))["content"] == "tom's"
