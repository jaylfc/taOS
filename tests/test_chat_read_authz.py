"""Red-first tests (tsk-5lnlrr): v1 chat reads enforce channel membership.

A non-admin session user who is not in a channel's ``members`` must get 403
from the v1 channel read routes; a member, an admin session and the host local
token keep full read access.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks

FORBIDDEN = {"error": "forbidden: not a member"}


async def _member_client(app, username="member") -> tuple[AsyncClient, str]:
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
async def test_v1_messages_non_member_forbidden(client, app):
    ch, _ = await _dm_with_message(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    try:
        resp = await member.get(f"/api/chat/channels/{ch['id']}/messages")
    finally:
        await member.aclose()
    assert resp.status_code == 403, resp.text
    assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
async def test_v1_get_channel_non_member_forbidden(client, app):
    ch, _ = await _dm_with_message(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    try:
        resp = await member.get(f"/api/chat/channels/{ch['id']}")
    finally:
        await member.aclose()
    assert resp.status_code == 403, resp.text
    assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
async def test_v1_messages_member_allowed(client, app):
    member, uid = await _member_client(app)
    ch, msg = await _dm_with_message(app, ["user", uid])
    try:
        resp = await member.get(f"/api/chat/channels/{ch['id']}/messages")
        resp_ch = await member.get(f"/api/chat/channels/{ch['id']}")
    finally:
        await member.aclose()
    assert resp.status_code == 200, resp.text
    assert [m["id"] for m in resp.json()["messages"]] == [msg["id"]]
    assert resp_ch.status_code == 200, resp_ch.text


@pytest.mark.asyncio
async def test_v1_messages_admin_session_allowed(client, app):
    ch, msg = await _dm_with_message(app, ["user", "agent-a"])
    resp = await client.get(f"/api/chat/channels/{ch['id']}/messages")
    assert resp.status_code == 200, resp.text
    assert [m["id"] for m in resp.json()["messages"]] == [msg["id"]]


@pytest.mark.asyncio
async def test_v1_messages_local_token_allowed(client, app):
    ch, msg = await _dm_with_message(app, ["user", "agent-a"])
    token = app.state.auth.get_local_token()
    bare = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        resp = await bare.get(f"/api/chat/channels/{ch['id']}/messages")
    finally:
        await bare.aclose()
    assert resp.status_code == 200, resp.text
    assert [m["id"] for m in resp.json()["messages"]] == [msg["id"]]
