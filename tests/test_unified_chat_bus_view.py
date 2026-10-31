"""Red-first tests for unified chat bus view router registration.

These tests MUST fail while the router is unregistered and MUST pass after
the router is registered under its own /api/chat/v2 prefix.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest


def _make_response(status_code, json_data, url="http://test"):
    req = httpx.Request("GET", url)
    return httpx.Response(status_code, json=json_data, request=req)


class _FakeBusClient:
    """Fake httpx.AsyncClient that returns canned bus responses."""

    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, **kwargs):
        for pattern, response in self._responses:
            if pattern in str(url):
                return _make_response(200, response, url=str(url))
        raise RuntimeError(f"Unexpected GET: {url}")

    async def post(self, url, json=None, **kwargs):
        for pattern, response in self._responses:
            if pattern in str(url):
                return _make_response(200, response, url=str(url))
        raise RuntimeError(f"Unexpected POST: {url}")


def _mock_async_client(responses):
    def _factory(*args, **kwargs):
        return _FakeBusClient(responses)
    return _factory


@pytest.mark.asyncio
async def test_v2_channels_served_by_bus_view(client):
    """GET /api/chat/v2/channels is answered by the unified bus view router.

    The bus view adds ``unified_bus: True`` to every channel it returns
    (both bus-backed and local-fallback paths).  The live chat router at
    /api/chat/channels never sets that field, so its presence is proof
    that the bus view handler answered.
    """
    bus_channels = [
        {"id": "ch1", "name": "bus channel"},
    ]
    mock_factory = _mock_async_client([
        ("/a2a/channels", {"channels": bus_channels}),
    ])

    with patch("httpx.AsyncClient", mock_factory):
        resp = await client.get("/api/chat/v2/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert "channels" in body
    channels = body["channels"]
    assert isinstance(channels, list)
    assert len(channels) >= 1
    assert any(ch.get("unified_bus") for ch in channels), (
        "no channel carried unified_bus: bus view was not the handler"
    )


@pytest.mark.asyncio
async def test_v2_channel_messages_proxies_to_bus(client):
    """GET /api/chat/v2/channels/{id}/messages proxies to the A2A bus.

    The bus is stubbed at the transport layer by replacing ``httpx.AsyncClient``
    with a fake that records calls and returns a canned bus response.  The
    response must round-trip through the registered app, not be served by a
    different handler.
    """
    bus_messages = [
        {"id": "bus-m1", "from": "alice", "body": "hello bus", "thread": "ch1"},
    ]
    mock_factory = _mock_async_client([
        ("/a2a/messages", {"messages": bus_messages}),
    ])

    with patch("httpx.AsyncClient", mock_factory):
        resp = await client.get("/api/chat/v2/channels/ch1/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["messages"]) == 1
    assert body["messages"][0]["unified_bus"] is True


@pytest.mark.asyncio
async def test_v2_handler_module_is_bus_view(client):
    """The /api/chat/v2/channels route module is chat_unified_bus_view."""
    resp = await client.get("/api/chat/v2/channels")
    assert resp.status_code == 200
    from tinyagentos.routes import chat_unified_bus_view as bus_view_mod
    app = client._transport.app
    matches = [r for r in app.routes if getattr(r, "path", "") == "/api/chat/v2/channels"]
    assert len(matches) == 1, f"expected exactly one handler, got {len(matches)}"
    assert matches[0].endpoint.__module__ == bus_view_mod.__name__


# ---- tsk-5lnlrr: v2 reads enforce caller authorization ----------------------

FORBIDDEN = {"error": "forbidden: not a member"}


def _bus_404():
    """Bus answers 404 for everything, so reads take the local-store fallback."""
    from fastapi import HTTPException

    async def _raise(*args, **kwargs):
        raise HTTPException(status_code=404, detail="bus channel not found")

    return patch("tinyagentos.routes.chat_unified_bus_view._proxy_to_bus", _raise)


class _CountingBus(_FakeBusClient):
    calls: list = []

    async def get(self, url, params=None, **kwargs):
        _CountingBus.calls.append(str(url))
        return await super().get(url, params=params, **kwargs)


def _counting_factory(responses):
    _CountingBus.calls = []

    def _factory(*args, **kwargs):
        return _CountingBus(responses)
    return _factory


async def _member_client(app, username="member"):
    from httpx import ASGITransport, AsyncClient
    from taos_test_csrf import csrf_event_hooks

    auth_mgr = app.state.auth
    rec = auth_mgr.find_user(username)
    if rec is None:
        code = auth_mgr.add_user_invite(username, "admin")
        auth_mgr.complete_invite(username, code, "Test Member", "", "memberauthzpass123")
        rec = auth_mgr.find_user(username)
    token = auth_mgr.create_session(user_id=rec["id"], long_lived=True)
    c = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )
    return c, rec["id"]


async def _dm_with_message(app, members):
    ch = await app.state.chat_channels.create_channel(
        name="dm", type="dm", created_by="user", members=members,
    )
    msg = await app.state.chat_messages.send_message(
        ch["id"], "user", "user", "private words",
    )
    return ch, msg


@pytest.mark.asyncio
async def test_v2_messages_non_member_forbidden(client, app):
    ch, _ = await _dm_with_message(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    factory = _counting_factory([("/a2a/messages", {"messages": [{"id": "b1", "thread": ch["id"]}]})])
    try:
        with patch("httpx.AsyncClient", factory):
            resp = await member.get(f"/api/chat/v2/channels/{ch['id']}/messages")
    finally:
        await member.aclose()
    assert resp.status_code == 403, resp.text
    assert resp.json() == FORBIDDEN
    assert _CountingBus.calls == [], "bus must not be reached for a non-member"


@pytest.mark.asyncio
async def test_v2_messages_non_member_forbidden_local_fallback(client, app):
    with _bus_404():
        ch, _ = await _dm_with_message(app, ["user", "agent-a"])
        member, _uid = await _member_client(app)
        try:
            resp = await member.get(f"/api/chat/v2/channels/{ch['id']}/messages")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
async def test_v2_message_by_id_non_member_forbidden(client, app):
    with _bus_404():
        ch, msg = await _dm_with_message(app, ["user", "agent-a"])
        member, _uid = await _member_client(app)
        try:
            resp = await member.get(f"/api/chat/v2/messages/{msg['id']}")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_v2_message_by_id_bus_non_member_forbidden(client, app):
    ch, _ = await _dm_with_message(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    factory = _counting_factory([("/a2a/messages/bus-m1", {"id": "bus-m1", "thread": ch["id"], "body": "x"})])
    try:
        with patch("httpx.AsyncClient", factory):
            resp = await member.get("/api/chat/v2/messages/bus-m1")
    finally:
        await member.aclose()
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_v2_channel_non_member_forbidden(client, app):
    with _bus_404():
        ch, _ = await _dm_with_message(app, ["user", "agent-a"])
        member, _uid = await _member_client(app)
        try:
            resp = await member.get(f"/api/chat/v2/channels/{ch['id']}")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
async def test_v2_unmapped_bus_thread_non_admin_forbidden(client, app):
    member, _uid = await _member_client(app)
    factory = _counting_factory([("/a2a/messages", {"messages": [{"id": "b1", "thread": "coord-thread"}]})])
    try:
        with patch("httpx.AsyncClient", factory):
            resp = await member.get("/api/chat/v2/channels/coord-thread/messages")
    finally:
        await member.aclose()
    assert resp.status_code == 403, resp.text
    assert _CountingBus.calls == []


@pytest.mark.asyncio
async def test_v2_unmapped_bus_thread_admin_allowed(client, app):
    factory = _counting_factory([("/a2a/messages", {"messages": [{"id": "b1", "thread": "coord-thread"}]})])
    with patch("httpx.AsyncClient", factory):
        resp = await client.get("/api/chat/v2/channels/coord-thread/messages")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["messages"]) == 1


@pytest.mark.asyncio
async def test_v2_member_allowed(client, app):
    with _bus_404():
        member, uid = await _member_client(app)
        ch, msg = await _dm_with_message(app, ["user", uid])
        try:
            resp = await member.get(f"/api/chat/v2/channels/{ch['id']}/messages")
            resp_msg = await member.get(f"/api/chat/v2/messages/{msg['id']}")
            resp_ch = await member.get(f"/api/chat/v2/channels/{ch['id']}")
        finally:
            await member.aclose()
        assert resp.status_code == 200, resp.text
        assert [m["id"] for m in resp.json()["messages"]] == [msg["id"]]
        assert resp_msg.status_code == 200, resp_msg.text
        assert resp_ch.status_code == 200, resp_ch.text


@pytest.mark.asyncio
async def test_v2_admin_reads_any_channel(client, app):
    with _bus_404():
        ch, msg = await _dm_with_message(app, ["user", "agent-a"])
        resp = await client.get(f"/api/chat/v2/channels/{ch['id']}/messages")
        resp_msg = await client.get(f"/api/chat/v2/messages/{msg['id']}")
        assert resp.status_code == 200, resp.text
        assert resp_msg.status_code == 200, resp_msg.text


@pytest.mark.asyncio
async def test_v2_list_channels_member_sees_only_own(client, app):
    member, uid = await _member_client(app)
    mine, _ = await _dm_with_message(app, ["user", uid])
    other, _ = await _dm_with_message(app, ["user", "agent-a"])
    try:
        with _bus_404():
            resp = await member.get("/api/chat/v2/channels")
    finally:
        await member.aclose()
    assert resp.status_code == 200, resp.text
    ids = {c["id"] for c in resp.json()["channels"]}
    assert mine["id"] in ids
    assert other["id"] not in ids


@pytest.mark.asyncio
async def test_v2_list_channels_bus_path_filters_non_member(client, app):
    member, uid = await _member_client(app)
    bus_channels = [
        {"id": "mine", "members": [uid]},
        {"id": "theirs", "members": ["user", "agent-a"]},
        {"id": "coord"},
    ]
    factory = _counting_factory([("/a2a/channels", {"channels": bus_channels})])
    try:
        with patch("httpx.AsyncClient", factory):
            resp = await member.get("/api/chat/v2/channels")
            admin_resp = await client.get("/api/chat/v2/channels")
    finally:
        await member.aclose()
    assert [c["id"] for c in resp.json()["channels"]] == ["mine"]
    assert len(admin_resp.json()["channels"]) == 3


# ---- tsk-2yunfy: v2 `before` must not be forwarded as the bus `since` cursor --

BEFORE_400 = {
    "error": "before is not supported on the bus view yet; page forward with since (message ts)"
}


@pytest.mark.asyncio
async def test_v2_messages_before_is_not_forwarded_as_since(client):
    from unittest.mock import AsyncMock

    mock_bus = AsyncMock(return_value={"messages": [{"id": "b1", "thread": "coord"}]})
    with patch("tinyagentos.routes.chat_unified_bus_view._proxy_to_bus", mock_bus):
        resp = await client.get("/api/chat/v2/channels/coord/messages?before=1234.5")
    assert resp.status_code == 400, resp.text
    assert resp.json() == BEFORE_400
    for call in mock_bus.call_args_list:
        params = call.kwargs.get("params") or {}
        assert params.get("since") != 1234.5, "before leaked into the bus since cursor"


@pytest.mark.asyncio
async def test_v2_messages_since_is_forwarded_to_bus(client):
    from unittest.mock import AsyncMock

    mock_bus = AsyncMock(return_value={"messages": [{"id": "b2", "thread": "coord"}]})
    with patch("tinyagentos.routes.chat_unified_bus_view._proxy_to_bus", mock_bus):
        resp = await client.get("/api/chat/v2/channels/coord/messages?since=1234.5")
    assert resp.status_code == 200, resp.text
    assert mock_bus.call_args.kwargs["params"]["since"] == 1234.5


@pytest.mark.asyncio
async def test_v2_messages_before_still_pages_local_fallback(client, app):
    ch, msg = await _dm_with_message(app, ["user", "agent-a"])
    with _bus_404():
        resp = await client.get(
            f"/api/chat/v2/channels/{ch['id']}/messages?before={msg['created_at'] + 1}"
        )
    assert resp.status_code == 200, resp.text
    assert [m["id"] for m in resp.json()["messages"]] == [msg["id"]]
