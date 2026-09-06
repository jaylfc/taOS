import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from taos_test_csrf import csrf_event_hooks
from tinyagentos.chat.message_store import ChatMessageStore


def _make_pins_app(tmp_path):
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


async def _authed_pins_client(tmp_path):
    app = _make_pins_app(tmp_path)
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
    return app, client, rec


@pytest.mark.asyncio
async def test_pin_and_list_pins(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="hi",
    )
    await store.pin_message("c1", msg["id"], pinned_by="user:jay")
    pins = await store.get_pins("c1")
    assert len(pins) == 1
    assert pins[0]["id"] == msg["id"]
    assert pins[0]["pinned_by"] == "user:jay"
    assert pins[0]["pinned_at"] is not None


@pytest.mark.asyncio
async def test_unpin_message(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="hi",
    )
    await store.pin_message("c1", msg["id"], pinned_by="user:jay")
    ok = await store.unpin_message("c1", msg["id"])
    assert ok is True
    pins = await store.get_pins("c1")
    assert pins == []
    # unpin again -> False
    ok2 = await store.unpin_message("c1", msg["id"])
    assert ok2 is False


@pytest.mark.asyncio
async def test_pin_cap_at_50(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    for i in range(50):
        m = await store.send_message(
            channel_id="c1", author_id="tom", author_type="agent", content=f"m{i}",
        )
        await store.pin_message("c1", m["id"], pinned_by="user:jay")
    # 51st should raise
    m51 = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="m51",
    )
    with pytest.raises(ValueError, match="pin cap"):
        await store.pin_message("c1", m51["id"], pinned_by="user:jay")


@pytest.mark.asyncio
async def test_pin_idempotent(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    m = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="x",
    )
    await store.pin_message("c1", m["id"], pinned_by="user:jay")
    await store.pin_message("c1", m["id"], pinned_by="user:jay")  # no raise
    pins = await store.get_pins("c1")
    assert len(pins) == 1


@pytest.mark.asyncio
async def test_is_pinned(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    m = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="x",
    )
    assert await store.is_pinned(m["id"]) is False
    await store.pin_message("c1", m["id"], pinned_by="user:jay")
    assert await store.is_pinned(m["id"]) is True


@pytest.mark.asyncio
async def test_get_pins_endpoint(tmp_path):
    app, client, _ = await _authed_pins_client(tmp_path)
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
                  "content": "pin me", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        await app.state.chat_messages.pin_message(ch_id, msg_id, pinned_by="user:admin")
        r = await client.get(f"/api/chat/channels/{ch_id}/pins")
        assert r.status_code == 200
        body = r.json()
        assert "pins" in body
        assert len(body["pins"]) == 1
        assert body["pins"][0]["id"] == msg_id
        assert body["pins"][0]["pinned_by"] == "user:admin"


@pytest.mark.asyncio
async def test_pin_endpoint_success(tmp_path):
    app, client, _ = await _authed_pins_client(tmp_path)
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
                  "content": "pin me", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        r = await client.post(f"/api/chat/messages/{msg_id}/pin")
        assert r.status_code == 200, r.json()
        pins_r = await client.get(f"/api/chat/channels/{ch_id}/pins")
        assert len(pins_r.json()["pins"]) == 1


@pytest.mark.asyncio
async def test_unpin_endpoint(tmp_path):
    app, client, _ = await _authed_pins_client(tmp_path)
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
                  "content": "x", "content_type": "text"},
        )
        msg_id = m_r.json()["id"]
        await app.state.chat_messages.pin_message(ch_id, msg_id, pinned_by="user:admin")
        r = await client.delete(f"/api/chat/messages/{msg_id}/pin")
        assert r.status_code == 204
        pins_r = await client.get(f"/api/chat/channels/{ch_id}/pins")
        assert pins_r.json()["pins"] == []


@pytest.mark.asyncio
async def test_pin_nonexistent_message_returns_404(tmp_path):
    app, client, _ = await _authed_pins_client(tmp_path)
    async with client:
        r = await client.post("/api/chat/messages/nonexistent/pin")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_pin_cap_returns_409(tmp_path):
    app, client, _ = await _authed_pins_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        for i in range(50):
            m = await app.state.chat_messages.send_message(
                channel_id=ch_id, author_id="user", author_type="user", content=f"m{i}",
            )
            await app.state.chat_messages.pin_message(ch_id, m["id"], pinned_by="user:admin")
        m51 = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="user", author_type="user", content="m51",
        )
        r = await client.post(f"/api/chat/messages/{m51['id']}/pin")
        assert r.status_code == 409
