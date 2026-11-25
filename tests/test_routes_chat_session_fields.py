"""Tests: every chat memory write carries session_id, turn_index and
session_date onto every turn it archives into taOSmd.

turn_index is derived from the stored conversation (count of messages already
in the channel), not an in-memory dict, so it survives a restart.

A real replay path test re-ingests an existing conversation into a fresh
channel and verifies each turn keeps its original turn_index and its own
session_date (the message's own timestamp, never ingest time).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tinyagentos.routes.chat import _session_date_iso


def _host_token_client(app):
    """Return a client presenting the host local token (no agent binding)."""
    from httpx import ASGITransport, AsyncClient

    token = app.state.auth.get_local_token()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _create_channel(app, name="general"):
    ch_store = app.state.chat_channels
    return await ch_store.create_channel(name=name, type="text", created_by="user")


async def _post_user_message(app, channel_id, content):
    async with _host_token_client(app) as c:
        resp = await c.post(
            "/api/chat/messages",
            json={
                "channel_id": channel_id,
                "author_id": "user",
                "author_type": "user",
                "content": content,
            },
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _archive_data(app):
    return app.state.archive.record.call_args.kwargs["data"]


@pytest.mark.asyncio
async def test_chat_archive_carries_session_fields(client):
    app = client._transport.app
    app.state.archive = AsyncMock()

    ch = await _create_channel(app, "session-fields")
    channel_id = ch["id"]

    message = await _post_user_message(app, channel_id, "hello session")
    created_at = message["created_at"]

    app.state.archive.record.assert_called_once()
    kwargs = app.state.archive.record.call_args.kwargs
    assert kwargs["event_type"] == "conversation"
    data = kwargs["data"]
    assert data["session_id"] == channel_id
    assert data["turn_index"] == 0
    assert data["session_date"] == _session_date_iso(created_at)


@pytest.mark.asyncio
async def test_chat_archive_turn_index_monotonic(client):
    app = client._transport.app
    app.state.archive = AsyncMock()

    ch = await _create_channel(app, "turn-index")
    channel_id = ch["id"]

    for i in range(3):
        await _post_user_message(app, channel_id, f"msg {i}")

    calls = app.state.archive.record.call_args_list
    assert len(calls) == 3
    assert calls[0].kwargs["data"]["turn_index"] == 0
    assert calls[1].kwargs["data"]["turn_index"] == 1
    assert calls[2].kwargs["data"]["turn_index"] == 2


@pytest.mark.asyncio
async def test_chat_replay_keeps_turn_index_and_date(client):
    """Re-ingesting an existing conversation keeps each turn's original
    turn_index and its own session_date (never re-dated to ingest time)."""
    app = client._transport.app
    app.state.archive = AsyncMock()

    ch_orig = await _create_channel(app, "replay-orig")
    ch_copy = await _create_channel(app, "replay-copy")

    contents = ["turn one", "turn two", "turn three"]

    for content in contents:
        await _post_user_message(app, ch_orig["id"], content)
    orig = [
        (call.kwargs["data"]["turn_index"], call.kwargs["data"]["content"])
        for call in app.state.archive.record.call_args_list
    ]
    assert [t[0] for t in orig] == [0, 1, 2]
    assert [t[1] for t in orig] == contents

    app.state.archive.reset_mock()

    copy_msgs = []
    for content in contents:
        copy_msgs.append(await _post_user_message(app, ch_copy["id"], content))
    copy = [
        (call.kwargs["data"]["turn_index"], call.kwargs["data"]["content"])
        for call in app.state.archive.record.call_args_list
    ]
    assert copy == orig

    # Each replayed turn keeps its own session_date (its message's created_at),
    # never a shared ingest timestamp.
    for msg, call in zip(copy_msgs, app.state.archive.record.call_args_list):
        assert call.kwargs["data"]["session_date"] == _session_date_iso(msg["created_at"])


@pytest.mark.asyncio
async def test_chat_turn_index_derived_from_stored_conversation(client):
    """The turn index is derived from the stored conversation (count of
    existing messages in the channel), so it survives a restart instead of
    being reset by an in-memory counter."""
    app = client._transport.app
    app.state.archive = AsyncMock()

    ch = await _create_channel(app, "derived-index")
    channel_id = ch["id"]
    msg_store = app.state.chat_messages

    # Seed two existing messages directly into the stored conversation.
    for content in ["seeded one", "seeded two"]:
        await msg_store.send_message(
            channel_id=channel_id,
            author_id="user",
            author_type="user",
            content=content,
        )

    message = await _post_user_message(app, channel_id, "new turn")
    assert _archive_data(app)["turn_index"] == 2
    assert _archive_data(app)["session_id"] == channel_id
    assert _archive_data(app)["session_date"] == _session_date_iso(
        message["created_at"]
    )
