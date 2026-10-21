"""Red-first tests (tsk-6jn7dt): the remaining v1 chat reads enforce membership.

A non-admin session user who is not in a channel's ``members`` gets 403 from
the message-by-id, thread and pins reads, and does not see that channel's
content in search, unread counts or the channel list. A member gets 200 and
sees the content; an admin session keeps full access.
"""
from __future__ import annotations

import pytest

from test_chat_read_authz import FORBIDDEN, _member_client


async def _dm_with_thread(app, members):
    ch = await app.state.chat_channels.create_channel(
        name="dm", type="dm", created_by="user", members=members,
    )
    parent = await app.state.chat_messages.send_message(
        ch["id"], "user", "user", "zebracorn private words",
    )
    reply = await app.state.chat_messages.send_message(
        ch["id"], "user", "user", "zebracorn private reply", thread_id=parent["id"],
    )
    await app.state.chat_messages.pin_message(ch["id"], parent["id"], "user")
    return ch, parent, reply


def _paths(ch, parent):
    return [
        f"/api/chat/messages/{parent['id']}",
        f"/api/chat/channels/{ch['id']}/threads/{parent['id']}/messages",
        f"/api/chat/channels/{ch['id']}/threads",
        f"/api/chat/channels/{ch['id']}/pins",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", [0, 1, 2, 3], ids=["message", "thread_messages", "threads", "pins"])
async def test_v1_reads_non_member_forbidden(client, app, idx):
    ch, parent, _reply = await _dm_with_thread(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    try:
        resp = await member.get(_paths(ch, parent)[idx])
    finally:
        await member.aclose()
    assert resp.status_code == 403, resp.text
    assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
async def test_v1_reads_member_allowed(client, app):
    member, uid = await _member_client(app)
    ch, parent, reply = await _dm_with_thread(app, ["user", uid])
    try:
        resps = [await member.get(p) for p in _paths(ch, parent)]
    finally:
        await member.aclose()
    for r in resps:
        assert r.status_code == 200, r.text
    assert resps[0].json()["id"] == parent["id"]
    assert [m["id"] for m in resps[1].json()["messages"]] == [reply["id"]]
    assert [t["id"] for t in resps[2].json()["threads"]] == [parent["id"]]
    assert [p["id"] for p in resps[3].json()["pins"]] == [parent["id"]]


@pytest.mark.asyncio
async def test_v1_search_hides_non_member_channel(client, app):
    ch, _parent, _reply = await _dm_with_thread(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    try:
        resp = await member.get("/api/chat/search", params={"q": "zebracorn"})
        scoped = await member.get(
            "/api/chat/search", params={"q": "zebracorn", "channel_id": ch["id"]},
        )
    finally:
        await member.aclose()
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == []
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["results"] == []


@pytest.mark.asyncio
async def test_v1_search_member_and_admin_see_results(client, app):
    member, uid = await _member_client(app)
    ch, _parent, _reply = await _dm_with_thread(app, ["user", uid])
    try:
        resp = await member.get("/api/chat/search", params={"q": "zebracorn"})
    finally:
        await member.aclose()
    admin = await client.get("/api/chat/search", params={"q": "zebracorn"})
    assert resp.status_code == 200, resp.text
    assert {r["channel_id"] for r in resp.json()["results"]} == {ch["id"]}
    assert len(resp.json()["results"]) == 2
    assert len(admin.json()["results"]) == 2


@pytest.mark.asyncio
async def test_v1_unread_hides_non_member_channel(client, app):
    ch, _parent, _reply = await _dm_with_thread(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    try:
        resp = await member.get("/api/chat/unread")
    finally:
        await member.aclose()
    admin = await client.get("/api/chat/unread")
    assert resp.status_code == 200, resp.text
    assert ch["id"] not in resp.json()["unread"]
    assert ch["id"] in admin.json()["unread"]


@pytest.mark.asyncio
async def test_v1_unread_member_sees_channel(client, app):
    member, uid = await _member_client(app)
    ch, _parent, _reply = await _dm_with_thread(app, ["user", uid])
    try:
        resp = await member.get("/api/chat/unread")
    finally:
        await member.aclose()
    assert resp.status_code == 200, resp.text
    assert resp.json()["unread"].get(ch["id"]) == 2


@pytest.mark.asyncio
async def test_v1_channel_list_hides_non_member_channel(client, app):
    ch, _parent, _reply = await _dm_with_thread(app, ["user", "agent-a"])
    member, _uid = await _member_client(app)
    try:
        resp = await member.get("/api/chat/channels")
    finally:
        await member.aclose()
    admin = await client.get("/api/chat/channels")
    assert resp.status_code == 200, resp.text
    listed = resp.json()["channels"]
    assert ch["id"] not in {c["id"] for c in listed}
    assert not any("zebracorn" in (c.get("last_preview") or "") for c in listed)
    assert ch["id"] in {c["id"] for c in admin.json()["channels"]}


@pytest.mark.asyncio
async def test_v1_channel_list_member_sees_preview(client, app):
    member, uid = await _member_client(app)
    ch, _parent, _reply = await _dm_with_thread(app, ["user", uid])
    try:
        resp = await member.get("/api/chat/channels")
    finally:
        await member.aclose()
    assert resp.status_code == 200, resp.text
    mine = [c for c in resp.json()["channels"] if c["id"] == ch["id"]]
    assert len(mine) == 1
    assert "zebracorn" in mine[0]["last_preview"]
