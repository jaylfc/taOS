"""Red-first tests (tsk-6jn7dt): the remaining v1 chat reads enforce membership.

A non-admin session user who is not in a channel's ``members`` gets 403 from
the message-by-id, thread and pins reads, and does not see that channel's
content in search, unread counts or the channel list. A member gets 200 and
sees the content; an admin session keeps full access.
"""
from __future__ import annotations

import pytest
import time

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


@pytest.mark.asyncio
async def test_search_limit_respects_readable_channels_before_limit(client, app):
    """Search should return readable results up to limit, not waste limit on unreadable channels."""
    # Create a member user
    member, uid = await _member_client(app)

    # Create a readable channel (member is in it)
    readable_ch = await app.state.chat_channels.create_channel(
        name="readable", type="dm", created_by="user", members=["user", uid],
    )

    # Create an unreadable channel (member is NOT in it)
    unreadable_ch = await app.state.chat_channels.create_channel(
        name="unreadable", type="dm", created_by="user", members=["user", "agent-a"],
    )

    # Add `limit` newer matching messages to the unreadable channel
    limit = 5
    search_term = "needleunique123"
    now = time.time()
    for i in range(limit):
        await app.state.chat_messages.ensure_message({
            "id": f"unreadable-{i}",
            "channel_id": unreadable_ch["id"],
            "author_id": "user",
            "author_type": "user",
            "content": f"{search_term} unreadable {i}",
            "created_at": now + i + 1,  # newer timestamps
        })

    # Add 1 older matching message to the readable channel
    await app.state.chat_messages.ensure_message({
        "id": "readable-0",
        "channel_id": readable_ch["id"],
        "author_id": "user",
        "author_type": "user",
        "content": f"{search_term} readable",
        "created_at": now,  # older timestamp
    })

    try:
        # Search with the limit - should return the 1 readable message
        resp = await member.get("/api/chat/search", params={"q": search_term, "limit": limit})
    finally:
        await member.aclose()

    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    # Should get the readable message, not 0 results (which would happen if
    # unreadable messages ate the LIMIT before filtering)
    assert len(results) == 1, f"Expected 1 readable result, got {len(results)}"
    assert results[0]["channel_id"] == readable_ch["id"]
    assert "readable" in results[0]["content"]


@pytest.mark.asyncio
async def test_search_excludes_channel_matching_member_only_case_insensitively(client, app):
    """SQLite LIKE is case-insensitive, so list_channels may return a channel
    whose stored member id differs from the caller's only by case. The route
    must re-check with caller_can_read_channel (exact str(uid) in members)
    so such channels are not searched."""
    member, uid = await _member_client(app)
    other = uid.swapcase()
    assert other != uid  # precondition: test must never pass vacuously

    search_term = "casevariantneedle999"
    now = time.time()

    # Channel the caller is NOT a real member of (id matches only case-insensitively)
    excluded_ch = await app.state.chat_channels.create_channel(
        name="excluded", type="dm", created_by="user", members=["user", other],
    )
    await app.state.chat_messages.ensure_message({
        "id": "excluded-msg",
        "channel_id": excluded_ch["id"],
        "author_id": "user",
        "author_type": "user",
        "content": f"{search_term} excluded",
        "created_at": now,
    })

    try:
        # Search while only the excluded channel exists: should return nothing
        # after the exact check filters it out. Before the fix, the case-
        # insensitive LIKE returns this channel and its message leaks through.
        resp = await member.get("/api/chat/search", params={"q": search_term})
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"] == []

        # Control: a readable channel (exact member match) with the same term
        # IS returned -- exactly one result, that channel id.
        control_ch = await app.state.chat_channels.create_channel(
            name="control", type="dm", created_by="user", members=["user", uid],
        )
        await app.state.chat_messages.ensure_message({
            "id": "control-msg",
            "channel_id": control_ch["id"],
            "author_id": "user",
            "author_type": "user",
            "content": f"{search_term} control",
            "created_at": now,
        })

        control_resp = await member.get("/api/chat/search", params={"q": search_term})
        assert control_resp.status_code == 200, control_resp.text
        control_results = control_resp.json()["results"]
        assert len(control_results) == 1, f"Expected 1 control result, got {len(control_results)}"
        assert control_results[0]["channel_id"] == control_ch["id"]
    finally:
        await member.aclose()
