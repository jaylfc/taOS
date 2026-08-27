import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from taos_test_csrf import csrf_event_hooks
from unittest.mock import AsyncMock, MagicMock
from tinyagentos.chat.message_store import ChatMessageStore
from tinyagentos.chat.threads import resolve_thread_recipients


def _ch(members, muted=None):
    return {
        "id": "c1", "type": "group", "members": members,
        "settings": {"muted": muted or []},
    }


@pytest.mark.asyncio
async def test_narrow_scope_parent_author_and_mentions():
    """parent=tom (agent), mentions @linus: recipients = {tom, linus}."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {
        "author_id": "user", "author_type": "user",
        "content": "@linus thoughts?", "thread_id": "p1",
    }
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm,
    )
    assert sorted(recipients) == ["linus", "tom"]
    assert forced["linus"] is True
    # tom (parent author) is a recipient but not force_respond unless mentioned
    assert forced.get("tom") is not True


@pytest.mark.asyncio
async def test_narrow_scope_prior_repliers():
    """Thread already has don as a replier → don is a recipient even if not mentioned."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[
        {"author_id": "don", "author_type": "agent"},
    ])
    msg = {"author_id": "user", "author_type": "user",
           "content": "more thoughts?", "thread_id": "p1"}
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm,
    )
    assert "don" in recipients


@pytest.mark.asyncio
async def test_at_all_escalates_to_all_channel_agents():
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {"author_id": "user", "author_type": "user",
           "content": "@all weigh in", "thread_id": "p1"}
    recipients, forced = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don", "linus"]), cm,
    )
    assert sorted(recipients) == ["don", "linus", "tom"]
    assert all(forced[s] is True for s in recipients)


@pytest.mark.asyncio
async def test_muted_agent_excluded_from_thread():
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {"author_id": "user", "author_type": "user",
           "content": "hi", "thread_id": "p1"}
    recipients, _ = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"], muted=["tom"]), cm,
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_author_never_recipient():
    """Agent tom replies in a thread → tom is not re-notified."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[
        {"author_id": "tom", "author_type": "agent"},
    ])
    msg = {"author_id": "tom", "author_type": "agent",
           "content": "follow-up", "thread_id": "p1"}
    recipients, _ = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm,
    )
    assert "tom" not in recipients


@pytest.mark.asyncio
async def test_user_parent_author_not_recipient():
    """Parent authored by user → parent-author rule adds nobody (user is not an agent)."""
    cm = MagicMock()
    cm.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "user", "author_type": "user",
    })
    cm.get_thread_messages = AsyncMock(return_value=[])
    msg = {"author_id": "user", "author_type": "user",
           "content": "kickoff", "thread_id": "p1"}
    recipients, _ = await resolve_thread_recipients(
        msg, _ch(["user", "tom", "don"]), cm,
    )
    # No agent has opted in yet, no mentions → empty recipients
    assert recipients == []


@pytest.mark.asyncio
async def test_get_thread_messages_returns_replies_oldest_first(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    parent = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="parent", content_type="text", state="complete", metadata=None,
    )
    r1 = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="r1", content_type="text", state="complete", metadata=None,
        thread_id=parent["id"],
    )
    r2 = await store.send_message(
        channel_id="c1", author_id="don", author_type="agent",
        content="r2", content_type="text", state="complete", metadata=None,
        thread_id=parent["id"],
    )
    msgs = await store.get_thread_messages(channel_id="c1", parent_id=parent["id"], limit=20)
    assert [m["id"] for m in msgs] == [r1["id"], r2["id"]]
    # parent is NOT included
    assert all(m["id"] != parent["id"] for m in msgs)


def _make_threads_app(tmp_path):
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


async def _authed_thread_client(tmp_path):
    """Create app + authenticated client with DB stores pre-initialized.
    ASGITransport in httpx 0.28 does not fire lifespan events, so we init
    the chat stores manually before returning."""
    app = _make_threads_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )
    return app, client


@pytest.mark.asyncio
async def test_get_thread_messages_endpoint(tmp_path):
    app, client = await _authed_thread_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        assert ch_r.status_code in (200, 201), ch_r.json()
        ch_id = ch_r.json()["id"]

        # post parent message
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "parent", "content_type": "text"},
        )
        assert r.status_code in (200, 201), r.json()
        parent_id = r.json()["id"]

        # post reply with thread_id
        r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "reply", "content_type": "text",
                  "thread_id": parent_id},
        )
        assert r.status_code in (200, 201), r.json()

        # fetch thread messages
        r = await client.get(f"/api/chat/channels/{ch_id}/threads/{parent_id}/messages")
        assert r.status_code == 200
        body = r.json()
        assert len(body["messages"]) == 1
        assert body["messages"][0]["content"] == "reply"


@pytest.mark.asyncio
async def test_get_message_by_id_endpoint(tmp_path):
    app, client = await _authed_thread_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "hello", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        got = await client.get(f"/api/chat/messages/{msg_id}")
        assert got.status_code == 200, got.json()
        assert got.json()["content"] == "hello"
        # 404 on unknown
        bad = await client.get("/api/chat/messages/nonexistent-id")
        assert bad.status_code == 404


@pytest.mark.asyncio
async def test_get_channel_threads_endpoint(tmp_path):
    app, client = await _authed_thread_client(tmp_path)
    async with client:
        ch_r = await client.post("/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"})
        ch_id = ch_r.json()["id"]
        p1 = await client.post("/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "parent one", "content_type": "text"})
        parent_id = p1.json()["id"]
        await client.post("/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "user", "author_type": "user",
                  "content": "reply", "content_type": "text", "thread_id": parent_id})
        r = await client.get(f"/api/chat/channels/{ch_id}/threads")
        assert r.status_code == 200
        data = r.json()
        assert len(data["threads"]) == 1
        assert data["threads"][0]["id"] == parent_id
        assert data["threads"][0]["reply_count"] == 1
