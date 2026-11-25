"""Tests: the chat route writes session_id / turn_index / session_date
onto every turn it archives into taOSmd (post_message -> archive.record).

These fail before the fix because the archive record carried none of the
three fields.  A replay test verifies a replayed turn keeps its original
turn_index and date (the import/replay path reseeds the per-channel counter
to the original next index before replaying).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tinyagentos.routes.chat import _TURN_INDEX_BY_CHANNEL, _session_date_iso


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
    """A replayed turn keeps its original turn_index and its own date.

    The import/replay path reseeds the per-channel counter to the original
    next index before replaying, so each replayed turn is numbered as it
    originally was, and each turn keeps its own session_date (the message's
    own timestamp, never ingest time).
    """
    app = client._transport.app
    app.state.archive = AsyncMock()

    ch = await _create_channel(app, "replay")
    channel_id = ch["id"]

    async with _host_token_client(app) as c:
        resp1 = await c.post(
            "/api/chat/messages",
            json={
                "channel_id": channel_id,
                "author_id": "user",
                "author_type": "user",
                "content": "original turn",
            },
        )
    assert resp1.status_code == 200, resp1.text
    data1 = app.state.archive.record.call_args.kwargs["data"]
    ti1 = data1["turn_index"]
    assert data1["session_date"] == _session_date_iso(resp1.json()["created_at"])

    # Replay: reseed the counter to the original next index, then re-write
    # the same turn. The replayed turn keeps its original index and its own
    # date (never re-dated to ingest time).
    _TURN_INDEX_BY_CHANNEL[channel_id] = ti1

    async with _host_token_client(app) as c:
        resp2 = await c.post(
            "/api/chat/messages",
            json={
                "channel_id": channel_id,
                "author_id": "user",
                "author_type": "user",
                "content": "original turn",
            },
        )
    assert resp2.status_code == 200, resp2.text
    data2 = app.state.archive.record.call_args.kwargs["data"]
    assert data2["turn_index"] == ti1
    assert data2["session_date"] == _session_date_iso(resp2.json()["created_at"])
