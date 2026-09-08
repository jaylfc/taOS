import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from taos_test_csrf import csrf_event_hooks


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


async def _authed_client(tmp_path, username="admin"):
    app = _make_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    app.state.auth.setup_user(username, f"{username} Name", "", "testpass")
    rec = app.state.auth.find_user(username)
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )
    return app, client, rec


@pytest.mark.asyncio
async def test_edit_own_message_sets_edited_at(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "v1", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r = await client.patch(
            f"/api/chat/messages/{msg_id}",
            json={"content": "v2"},
        )
        assert r.status_code == 200, r.json()
        assert r.json()["content"] == "v2"
        assert r.json()["edited_at"] is not None


@pytest.mark.asyncio
async def test_edit_non_own_returns_403(tmp_path):
    app, client, _ = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="tom", author_type="agent", content="tom's",
        )
        r = await client.patch(f"/api/chat/messages/{m['id']}", json={"content": "hacked"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_edit_rejects_non_content_fields(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "x", "content_type": "text"},
        )
        r = await client.patch(
            f"/api/chat/messages/{m_r.json()['id']}",
            json={"content": "ok", "thread_id": "evil"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_edit_deleted_message_returns_404(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "x", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        await app.state.chat_messages.soft_delete_message(msg_id)
        r = await client.patch(f"/api/chat/messages/{msg_id}", json={"content": "y"})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_own_returns_204_and_sets_deleted_at(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "bye", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r = await client.delete(f"/api/chat/messages/{msg_id}")
        assert r.status_code == 204
        got = await app.state.chat_messages.get_message(msg_id)
        assert got["deleted_at"] is not None


@pytest.mark.asyncio
async def test_delete_non_own_returns_403(tmp_path):
    app, client, _ = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="tom", author_type="agent", content="tom's",
        )
        r = await client.delete(f"/api/chat/messages/{m['id']}")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_idempotent(tmp_path):
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "x", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r1 = await client.delete(f"/api/chat/messages/{msg_id}")
        r2 = await client.delete(f"/api/chat/messages/{msg_id}")
        assert r1.status_code == 204
        assert r2.status_code == 204


@pytest.mark.asyncio
async def test_edit_truncates_subsequent_messages(tmp_path):
    """Editing a user message in a DM channel soft-deletes every message
    created after it in the same channel and thread, and broadcasts delete
    events for each."""
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "dm-chat", "type": "dm", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]

        # Post three messages: m1, m2, m3
        m1 = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "first", "content_type": "text"},
        )
        m2 = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": "tom", "author_type": "agent",
                  "content": "second", "content_type": "text"},
        )
        m3 = await client.post(
            "/api/chat/messages",
            json={"channel_id": ch_id, "author_id": rec["id"], "author_type": "user",
                  "content": "third", "content_type": "text"},
        )
        m1_id = m1.json()["id"]
        m2_id = m2.json()["id"]
        m3_id = m3.json()["id"]

        # Edit m1 — this should soft-delete m2 and m3
        r = await client.patch(
            f"/api/chat/messages/{m1_id}", json={"content": "first-edited"},
        )
        assert r.status_code == 200, r.json()
        assert r.json()["content"] == "first-edited"

        # m1 should still exist (edited, not deleted)
        got_m1 = await app.state.chat_messages.get_message(m1_id)
        assert got_m1["content"] == "first-edited"
        assert got_m1["deleted_at"] is None

        # m2 and m3 should be soft-deleted
        got_m2 = await app.state.chat_messages.get_message(m2_id)
        assert got_m2["deleted_at"] is not None
        got_m3 = await app.state.chat_messages.get_message(m3_id)
        assert got_m3["deleted_at"] is not None


@pytest.mark.asyncio
async def test_soft_delete_messages_after_empty_when_nothing_after(tmp_path):
    """soft_delete_messages_after returns an empty list when there are no
    messages created after the given timestamp."""
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        m = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id=rec["id"], author_type="user",
            content="only message",
        )
        store = app.state.chat_messages
        # After the message's own timestamp there should be nothing.
        ids = await store.soft_delete_messages_after(ch_id, m["created_at"])
        assert ids == []
        # The message itself should still be intact.
        got = await store.get_message(m["id"])
        assert got["deleted_at"] is None


@pytest.mark.asyncio
async def test_edit_only_truncates_own_channel(tmp_path):
    """Editing a message in DM channel A must not affect messages in
    DM channel B."""
    app, client, rec = await _authed_client(tmp_path)
    async with client:
        # Channel A
        ch_a = await client.post(
            "/api/chat/channels",
            json={"name": "dm-a", "type": "dm", "description": "", "topic": "",
                  "members": ["user", "alice"], "created_by": "user"},
        )
        a_id = ch_a.json()["id"]
        # Channel B
        ch_b = await client.post(
            "/api/chat/channels",
            json={"name": "dm-b", "type": "dm", "description": "", "topic": "",
                  "members": ["user", "bob"], "created_by": "user"},
        )
        b_id = ch_b.json()["id"]

        m_a = await client.post(
            "/api/chat/messages",
            json={"channel_id": a_id, "author_id": rec["id"], "author_type": "user",
                  "content": "a1", "content_type": "text"},
        )
        a2_r = await client.post(
            "/api/chat/messages",
            json={"channel_id": a_id, "author_id": rec["id"], "author_type": "user",
                  "content": "a2", "content_type": "text"},
        )
        a2_id = a2_r.json()["id"]
        m_b = await client.post(
            "/api/chat/messages",
            json={"channel_id": b_id, "author_id": rec["id"], "author_type": "user",
                  "content": "b1", "content_type": "text"},
        )

        # Edit m_a (channel A) — should truncate a2 but not b1
        r = await client.patch(
            f"/api/chat/messages/{m_a.json()['id']}", json={"content": "a1-edited"},
        )
        assert r.status_code == 200

        # a2 should be soft-deleted (it came after a1 in the same DM)
        got_a2 = await app.state.chat_messages.get_message(a2_id)
        assert got_a2["deleted_at"] is not None

        # b1 should still be intact
        got_b = await app.state.chat_messages.get_message(m_b.json()["id"])
        assert got_b["deleted_at"] is None
