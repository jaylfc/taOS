"""Tests: the chat route writes session_id / turn_index / session_date
onto every turn it archives into taOSmd (post_message -> archive.record).

turn_index is derived from the stored conversation (count of messages already
in the channel) so it survives a restart, never an in-memory dict.  session_date
is each turn's own created_at, never ingest time.

A replay test verifies re-ingesting an existing conversation keeps the original
indexes and dates.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tinyagentos.routes import chat as chat_module
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


@pytest.mark.asyncio
async def test_chat_archive_carries_session_fields(client):
    app = client._transport.app
    app.state.archive = AsyncMock()

    ch = await _create_channel(app, "session-fields")
    channel_id = ch["id"]

    async with _host_token_client(app) as c:
        resp = await c.post(
            "/api/chat/messages",
            json={
                "channel_id": channel_id,
                "author_id": "user",
                "author_type": "user",
                "content": "hello session",
            },
        )
    assert resp.status_code == 200, resp.text
    message = resp.json()
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

    async with _host_token_client(app) as c:
        for i in range(3):
            resp = await c.post(
                "/api/chat/messages",
                json={
                    "channel_id": channel_id,
                    "author_id": "user",
                    "author_type": "user",
                    "content": f"msg {i}",
                },
            )
            assert resp.status_code == 200, resp.text

    calls = app.state.archive.record.call_args_list
    assert len(calls) == 3
    assert calls[0].kwargs["data"]["turn_index"] == 0
    assert calls[1].kwargs["data"]["turn_index"] == 1
    assert calls[2].kwargs["data"]["turn_index"] == 2


@pytest.mark.asyncio
async def test_chat_replay_keeps_turn_index_and_date(client):
    """Re-ingesting an existing conversation keeps each turn's original
    turn_index and its own session_date (never re-dated to ingest time).

    Simulates a restart by swapping in a fresh archive mock (the in-memory
    counter no longer exists), then re-ingests the stored conversation through
    the real replay path.
    """
    app = client._transport.app
    app.state.archive = AsyncMock()

    ch = await _create_channel(app, "replay")
    channel_id = ch["id"]

    original = []
    async with _host_token_client(app) as c:
        for content in ["first turn", "second turn", "third turn"]:
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
            call = app.state.archive.record.call_args_list[-1]
            data = call.kwargs["data"]
            original.append(
                {
                    "turn_index": data["turn_index"],
                    "session_date": data["session_date"],
                    "content": data["content"],
                }
            )

    assert [o["turn_index"] for o in original] == [0, 1, 2]
    assert all(o["session_date"] for o in original)

    # Fresh archive mock simulates a restart: the per-session counter is gone
    # and turn_index must be re-derived from the stored conversation.
    app.state.archive = AsyncMock()

    # Resolve the replay path lazily so the module still imports on older code
    # that lacks `replay_conversation_into_archive`. The red run must be an
    # assertion failure (missing replay path), not an ImportError.
    replay = getattr(chat_module, "replay_conversation_into_archive", None)
    assert replay is not None, (
        "replay_conversation_into_archive is not implemented: "
        "re-ingest cannot preserve original indexes and dates"
    )

    await replay(app, channel_id)

    calls = app.state.archive.record.call_args_list
    assert len(calls) == len(original)
    for call, expected in zip(calls, original):
        data = call.kwargs["data"]
        assert data["session_id"] == channel_id
        assert data["turn_index"] == expected["turn_index"]
        assert data["session_date"] == expected["session_date"]
        assert data["content"] == expected["content"]
