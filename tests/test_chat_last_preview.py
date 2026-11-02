"""Tests for chat last_preview feature on GET /api/chat/channels."""
from __future__ import annotations

import pytest


def _host_token_client(app):
    """Return a client presenting the host local token (no agent binding).

    Mirrors the same helper in tests/test_chat_agent_author_binding.py. It is
    copied here rather than imported across test files: the session `client`
    fixture used to post messages with arbitrary author_ids, and with the
    author check scoped to browser sessions those are now test conveniences,
    not product flows, so each file keeps its own copy.
    """
    from httpx import ASGITransport, AsyncClient

    token = app.state.auth.get_local_token()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _create_channel(client, name="general"):
    """Helper: create a chat channel via the channel store directly."""
    app = client._transport.app
    ch_store = app.state.chat_channels
    return await ch_store.create_channel(
        name=name, type="text", created_by="user"
    )


async def _send_message(client, channel_id, author_id, content, author_type="user", thread_id=None):
    """Helper: send a message via HTTP through the host local token."""
    app = client._transport.app
    async with _host_token_client(app) as c:
        resp = await c.post("/api/chat/messages", json={
            "channel_id": channel_id,
            "author_id": author_id,
            "author_type": author_type,
            "content": content,
            "thread_id": thread_id,
        })
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_list_channels_returns_last_preview(client):
    """GET /api/chat/channels includes last_preview with the latest message text."""
    ch = await _create_channel(client, "preview-ch")

    # Send two messages
    await _send_message(client, ch["id"], "user-1", "First message")
    msg2 = await _send_message(client, ch["id"], "user-2", "Second message")

    # GET channels
    resp = await client.get("/api/chat/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]

    # Find our channel
    target = next(c for c in channels if c["id"] == ch["id"])
    assert "last_preview" in target, "Channel should have last_preview key"
    assert target["last_preview"] == "Second message", "Preview should be the latest message"
    assert target["last_preview_author"] == "user-2", "Preview should have author_id"


@pytest.mark.asyncio
async def test_last_preview_falls_back_when_newest_deleted(client):
    """Deleted newest message -> preview falls back to the previous one."""
    ch = await _create_channel(client, "fallback-ch")

    msg1 = await _send_message(client, ch["id"], "user-1", "First message")
    msg2 = await _send_message(client, ch["id"], "user-2", "Second message")

    # Soft-delete the newest message
    msg_store = client._transport.app.state.chat_messages
    await msg_store.soft_delete_message(msg2["id"])

    resp = await client.get("/api/chat/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    target = next(c for c in channels if c["id"] == ch["id"])

    assert target["last_preview"] == "First message", "Preview should fall back to previous non-deleted message"
    assert target["last_preview_author"] == "user-1"


@pytest.mark.asyncio
async def test_last_preview_ignores_thread_replies(client):
    """Thread reply (thread_id set) newer than top-level -> preview is top-level message."""
    ch = await _create_channel(client, "thread-ch")

    # Send top-level message
    parent = await _send_message(client, ch["id"], "user-1", "Parent message")
    # Send thread reply (newer)
    await _send_message(client, ch["id"], "user-2", "Thread reply", thread_id=parent["id"])

    resp = await client.get("/api/chat/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    target = next(c for c in channels if c["id"] == ch["id"])

    assert target["last_preview"] == "Parent message", "Preview should be top-level message, not thread reply"
    assert target["last_preview_author"] == "user-1"


@pytest.mark.asyncio
async def test_last_preview_truncates_at_120_chars(client):
    """A 300-char message -> preview length is 120."""
    ch = await _create_channel(client, "truncate-ch")

    long_content = "x" * 300
    await _send_message(client, ch["id"], "user-1", long_content)

    resp = await client.get("/api/chat/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    target = next(c for c in channels if c["id"] == ch["id"])

    assert len(target["last_preview"]) == 120, "Preview should be truncated to 120 chars"
    assert target["last_preview"] == "x" * 120


@pytest.mark.asyncio
async def test_channel_with_no_messages_has_no_last_preview_key(client):
    """Channel with no messages -> no last_preview key."""
    ch = await _create_channel(client, "empty-ch")

    resp = await client.get("/api/chat/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    target = next(c for c in channels if c["id"] == ch["id"])

    assert "last_preview" not in target, "Channel with no messages should not have last_preview key"
    assert "last_preview_author" not in target, "Channel with no messages should not have last_preview_author key"