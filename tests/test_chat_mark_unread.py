import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from taos_test_csrf import csrf_event_hooks
from tinyagentos.chat.channel_store import ChatChannelStore


@pytest.mark.asyncio
async def test_rewind_read_cursor_sets_last_read_at(tmp_path):
    store = ChatChannelStore(tmp_path / "channels.db")
    await store.init()
    await store.update_read_position("jay", "c1", "m5")
    # Force a known timestamp by directly verifying the row exists first,
    # then set last_read_at to a known value via mark_read approach.
    # Instead, manually set last_read_at to 1000.0 for this test.
    await store._db.execute(
        "UPDATE chat_read_positions SET last_read_at = 1000.0 WHERE user_id = ? AND channel_id = ?",
        ("jay", "c1"),
    )
    await store._db.commit()

    await store.rewind_read_cursor("jay", "c1", before_ts=500.0)
    async with store._db.execute(
        "SELECT last_read_at FROM chat_read_positions WHERE user_id = ? AND channel_id = ?",
        ("jay", "c1"),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 500.0


@pytest.mark.asyncio
async def test_rewind_without_prior_read_creates_row(tmp_path):
    store = ChatChannelStore(tmp_path / "channels.db")
    await store.init()
    # No prior update_read_position call
    await store.rewind_read_cursor("jay", "c1", before_ts=100.0)
    async with store._db.execute(
        "SELECT last_read_at FROM chat_read_positions WHERE user_id = ? AND channel_id = ?",
        ("jay", "c1"),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 100.0


def _make_unread_app(tmp_path):
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


async def _authed_unread_client(tmp_path):
    app = _make_unread_app(tmp_path)
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
async def test_rewind_endpoint_sets_cursor(tmp_path):
    app, client, rec = await _authed_unread_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="user", author_type="user", content="m1",
        )
        m2 = await app.state.chat_messages.send_message(
            channel_id=ch_id, author_id="user", author_type="user", content="m2",
        )
        # Call rewind to before m2
        r = await client.post(
            f"/api/chat/channels/{ch_id}/read-cursor/rewind",
            json={"before_message_id": m2["id"]},
        )
        assert r.status_code == 200, r.json()
        # Verify the cursor row was written at msg2.created_at - 0.001
        async with app.state.chat_channels._db.execute(
            "SELECT last_read_at FROM chat_read_positions WHERE user_id = ? AND channel_id = ?",
            (rec["id"], ch_id),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert abs(row[0] - (m2["created_at"] - 0.001)) < 0.0001


@pytest.mark.asyncio
async def test_rewind_unknown_message_returns_404(tmp_path):
    app, client, _ = await _authed_unread_client(tmp_path)
    async with client:
        ch_r = await client.post(
            "/api/chat/channels",
            json={"name": "g", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch_id = ch_r.json()["id"]
        r = await client.post(
            f"/api/chat/channels/{ch_id}/read-cursor/rewind",
            json={"before_message_id": "nonexistent"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_rewind_message_from_wrong_channel_returns_404(tmp_path):
    app, client, _ = await _authed_unread_client(tmp_path)
    async with client:
        ch1 = await client.post(
            "/api/chat/channels",
            json={"name": "g1", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch2 = await client.post(
            "/api/chat/channels",
            json={"name": "g2", "type": "group", "description": "", "topic": "",
                  "members": ["user", "tom"], "created_by": "user"},
        )
        ch1_id = ch1.json()["id"]
        ch2_id = ch2.json()["id"]
        # Message in ch1, but rewind against ch2
        m = await app.state.chat_messages.send_message(
            channel_id=ch1_id, author_id="user", author_type="user", content="m",
        )
        r = await client.post(
            f"/api/chat/channels/{ch2_id}/read-cursor/rewind",
            json={"before_message_id": m["id"]},
        )
        assert r.status_code == 404
