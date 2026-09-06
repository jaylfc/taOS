"""Tests for chat HTTP routes."""
from __future__ import annotations

import io

import pytest

from tinyagentos.chat.hub import ChatHub


async def _create_channel(client, name="general"):
    """Helper: create a chat channel via the channel store directly."""
    app = client._transport.app
    ch_store = app.state.chat_channels
    return await ch_store.create_channel(
        name=name, type="text", created_by="user"
    )


# ── POST /api/chat/messages ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_message(client):
    ch = await _create_channel(client)
    resp = await client.post("/api/chat/messages", json={
        "channel_id": ch["id"],
        "author_id": "agent-1",
        "author_type": "agent",
        "content": "Hello from agent",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"]
    assert body["content"] == "Hello from agent"
    assert body["channel_id"] == ch["id"]
    assert body["author_id"] == "agent-1"


@pytest.mark.asyncio
async def test_post_message_with_embeds(client):
    ch = await _create_channel(client)
    embeds = [{"title": "Test Embed", "description": "Some description"}]
    resp = await client.post("/api/chat/messages", json={
        "channel_id": ch["id"],
        "author_id": "agent-1",
        "content": "Message with embed",
        "embeds": embeds,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["embeds"] == embeds


@pytest.mark.asyncio
async def test_post_message_state_defaults_to_complete(client):
    ch = await _create_channel(client)
    resp = await client.post("/api/chat/messages", json={
        "channel_id": ch["id"],
        "author_id": "agent-1",
        "content": "Done",
    })
    assert resp.status_code == 200
    assert resp.json()["state"] == "complete"


@pytest.mark.asyncio
async def test_post_message_explicit_state(client):
    ch = await _create_channel(client)
    resp = await client.post("/api/chat/messages", json={
        "channel_id": ch["id"],
        "author_id": "agent-1",
        "content": "",
        "state": "streaming",
    })
    assert resp.status_code == 200
    assert resp.json()["state"] == "streaming"


# ── POST /api/chat/messages/{id}/delta ───────────────────────────────────────

@pytest.mark.asyncio
async def test_post_delta(client):
    ch = await _create_channel(client)
    create_resp = await client.post("/api/chat/messages", json={
        "channel_id": ch["id"],
        "author_id": "agent-1",
        "content": "",
        "state": "streaming",
    })
    msg_id = create_resp.json()["id"]

    resp = await client.post(f"/api/chat/messages/{msg_id}/delta", json={
        "channel_id": ch["id"],
        "delta": " world",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_post_delta_unknown_message(client):
    """Delta for non-existent message still returns 200 (broadcast is best-effort)."""
    resp = await client.post("/api/chat/messages/nonexistent/delta", json={
        "channel_id": "ch-x",
        "delta": "hi",
    })
    assert resp.status_code == 200


# ── POST /api/chat/messages/{id}/state ───────────────────────────────────────

@pytest.mark.asyncio
async def test_update_state(client):
    ch = await _create_channel(client)
    create_resp = await client.post("/api/chat/messages", json={
        "channel_id": ch["id"],
        "author_id": "agent-1",
        "content": "",
        "state": "streaming",
    })
    msg_id = create_resp.json()["id"]

    resp = await client.post(f"/api/chat/messages/{msg_id}/state", json={
        "state": "complete",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"

    # Verify the state was persisted
    app = client._transport.app
    msg_store = app.state.chat_messages
    msg = await msg_store.get_message(msg_id)
    assert msg["state"] == "complete"


@pytest.mark.asyncio
async def test_update_state_unknown_message(client):
    """State update for non-existent message returns 200 (graceful noop)."""
    resp = await client.post("/api/chat/messages/nonexistent/state", json={
        "state": "error",
    })
    assert resp.status_code == 200


# ── Hub is wired into app.state ───────────────────────────────────────────────

def test_chat_hub_in_app_state(app):
    assert hasattr(app.state, "chat_hub")
    assert isinstance(app.state.chat_hub, ChatHub)


# ── Channel CRUD ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_channel(client):
    resp = await client.post("/api/chat/channels", json={
        "name": "my-channel",
        "type": "topic",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"]
    assert body["name"] == "my-channel"
    assert body["type"] == "topic"


@pytest.mark.asyncio
async def test_list_channels(client):
    await client.post("/api/chat/channels", json={"name": "alpha", "type": "topic"})
    resp = await client.get("/api/chat/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    names = [c["name"] for c in channels]
    assert "alpha" in names


@pytest.mark.asyncio
async def test_get_channel(client):
    create_resp = await client.post("/api/chat/channels", json={"name": "beta", "type": "text"})
    ch_id = create_resp.json()["id"]

    resp = await client.get(f"/api/chat/channels/{ch_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == ch_id
    assert body["name"] == "beta"


@pytest.mark.asyncio
async def test_get_channel_not_found(client):
    resp = await client.get("/api/chat/channels/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "Channel not found"


@pytest.mark.asyncio
async def test_update_channel(client):
    create_resp = await client.post("/api/chat/channels", json={"name": "gamma", "type": "topic"})
    ch_id = create_resp.json()["id"]

    resp = await client.put(f"/api/chat/channels/{ch_id}", json={"description": "updated desc"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"


@pytest.mark.asyncio
async def test_delete_channel(client):
    create_resp = await client.post("/api/chat/channels", json={"name": "delta", "type": "topic"})
    ch_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/chat/channels/{ch_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    # Confirm gone
    get_resp = await client.get(f"/api/chat/channels/{ch_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_get_channel_messages(client):
    create_resp = await client.post("/api/chat/channels", json={"name": "msgs-ch", "type": "topic"})
    ch_id = create_resp.json()["id"]

    await client.post("/api/chat/messages", json={
        "channel_id": ch_id,
        "author_id": "user",
        "author_type": "user",
        "content": "hello channel",
    })

    resp = await client.get(f"/api/chat/channels/{ch_id}/messages")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) >= 1
    assert messages[0]["content"] == "hello channel"


# ── File upload ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_file(client):
    file_content = b"hello file content"
    resp = await client.post(
        "/api/chat/upload",
        files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"]
    assert body["filename"] == "test.txt"
    assert body["size"] == len(file_content)
    assert body["url"].startswith("/api/chat/files/")


# ── Search ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_messages(client):
    ch = await _create_channel(client, "search-ch")
    await client.post("/api/chat/messages", json={
        "channel_id": ch["id"],
        "author_id": "user",
        "author_type": "user",
        "content": "unique-searchterm-xyz",
    })

    resp = await client.get("/api/chat/search", params={"q": "unique-searchterm-xyz"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "unique-searchterm-xyz"
    assert len(body["results"]) >= 1
    assert body["results"][0]["content"] == "unique-searchterm-xyz"


@pytest.mark.asyncio
async def test_search_empty_query(client):
    resp = await client.get("/api/chat/search", params={"q": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []


# ── Unread counts ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unread_counts(client):
    resp = await client.get("/api/chat/unread")
    assert resp.status_code == 200
    assert "unread" in resp.json()



# ── ?archived filter on GET /api/chat/channels ────────────────────────────────

@pytest.mark.asyncio
async def test_list_channels_archived_false_excludes_archived(client):
    """?archived=false omits channels flagged archived."""
    app = client._transport.app
    ch_store = app.state.chat_channels

    live_ch = await ch_store.create_channel(name="live", type="dm", created_by="user")
    arch_ch = await ch_store.create_channel(name="archived-dm", type="dm", created_by="user")
    await ch_store.set_settings(
        arch_ch["id"],
        {"archived": True, "archived_at": "20260101T000000", "archived_agent_id": "ag1"},
    )

    resp = await client.get("/api/chat/channels", params={"archived": "false"})
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["channels"]}
    assert live_ch["id"] in ids
    assert arch_ch["id"] not in ids


@pytest.mark.asyncio
async def test_list_channels_archived_true_returns_only_archived(client):
    """?archived=true returns only archived channels."""
    app = client._transport.app
    ch_store = app.state.chat_channels

    live_ch = await ch_store.create_channel(name="live2", type="dm", created_by="user")
    arch_ch = await ch_store.create_channel(name="archived-dm2", type="dm", created_by="user")
    await ch_store.set_settings(
        arch_ch["id"],
        {"archived": True, "archived_at": "20260101T000000", "archived_agent_id": "ag2"},
    )

    resp = await client.get("/api/chat/channels", params={"archived": "true"})
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["channels"]}
    assert arch_ch["id"] in ids
    assert live_ch["id"] not in ids


@pytest.mark.asyncio
async def test_list_channels_no_archived_param_returns_all(client):
    """No ?archived param returns all channels (no filter)."""
    app = client._transport.app
    ch_store = app.state.chat_channels

    live_ch = await ch_store.create_channel(name="live3", type="dm", created_by="user")
    arch_ch = await ch_store.create_channel(name="archived-dm3", type="dm", created_by="user")
    await ch_store.set_settings(arch_ch["id"], {"archived": True})

    resp = await client.get("/api/chat/channels")
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["channels"]}
    assert live_ch["id"] in ids
    assert arch_ch["id"] in ids
