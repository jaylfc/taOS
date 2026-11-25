"""User memory is per person, and global notification mutes are admin only.

Before this, every capture and every route used one shared partition, so any
member could browse, search, change and delete everyone's captured chats,
notes and file activity. The old shared rows now belong to the install owner.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.user_memory import LEGACY_USER_ID, UserMemoryStore


@pytest_asyncio.fixture
async def store(client, tmp_path):
    s = client._transport.app.state.user_memory
    if s._db is not None:
        await s.close()
    s.db_path = tmp_path / "user_memory.db"
    await s.init()
    yield s
    await s.close()


async def _member(app, name="mem"):
    auth = app.state.auth
    code = auth.add_user_invite(name, "admin")
    auth.complete_invite(name, code, name.title(), "", "memberumpass123")
    rec = auth.find_user(name)
    token = auth.create_session(user_id=rec["id"], long_lived=True)
    c = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        cookies={"taos_session": token}, event_hooks=csrf_event_hooks(),
    )
    return c, rec["id"]


@pytest.mark.asyncio
async def test_member_cannot_read_search_or_delete_the_owners_memory(client, app, store):
    owner_id = app.state.auth.get_primary_user()["id"]
    h = await store.save_chunk(owner_id, "the vault code is hunter2", "secret", "conversations")
    member, member_id = await _member(app)
    try:
        assert (await member.get("/api/user-memory/browse")).json()["chunks"] == []
        assert (await member.get("/api/user-memory/search?q=hunter2")).json()["results"] == []
        assert (await member.get("/api/user-memory/stats")).json()["total"] == 0
        assert (await member.delete(f"/api/user-memory/chunk/{h}")).json() == {"ok": False}
        # The owner's chunk is still there, and the owner still sees it.
        chunks = (await client.get("/api/user-memory/browse")).json()["chunks"]
        assert [c["hash"] for c in chunks] == [h]
        # A member's own save is theirs alone.
        await member.post("/api/user-memory/save", json={"content": "mine", "collection": "snippets"})
        assert len((await member.get("/api/user-memory/browse")).json()["chunks"]) == 1
        assert len((await client.get("/api/user-memory/browse")).json()["chunks"]) == 1
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_settings_are_per_user(client, app, store):
    member, _ = await _member(app)
    try:
        r = await member.put("/api/user-memory/settings", json={"capture_searches": True})
        assert r.status_code == 200
        assert (await member.get("/api/user-memory/settings")).json()["capture_searches"] is True
        assert (await client.get("/api/user-memory/settings")).json()["capture_searches"] is False
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_agent_search_is_fenced_to_the_agents_owner(client, app, store):
    owner_id = app.state.auth.get_primary_user()["id"]
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    try:
        row = await reg.register(framework="none", display_name="Helper", user_id=owner_id)
        app.state.config.agents.append({
            "name": "helper", "registry_canonical_id": row["canonical_id"],
            "can_read_user_memory": True,
        })
        await store.save_chunk(owner_id, "pineapple notes", "n", "notes")
        member, _ = await _member(app)
        try:
            denied = await member.get("/api/user-memory/agent-search?q=pineapple&agent_name=helper")
            assert denied.status_code == 403
        finally:
            await member.aclose()
        ok = await client.get("/api/user-memory/agent-search?q=pineapple&agent_name=helper")
        assert ok.status_code == 200 and len(ok.json()["results"]) == 1
    finally:
        await reg.close()


@pytest.mark.asyncio
async def test_legacy_shared_rows_move_to_the_owner_and_are_idempotent(tmp_path):
    s = UserMemoryStore(tmp_path / "um.db")
    await s.init()
    try:
        await s.save_chunk(LEGACY_USER_ID, "old chat", "t", "conversations")
        await s.update_settings(LEGACY_USER_ID, {"capture_searches": True})
        assert await s.rekey_legacy("owner-1") == 1
        assert len(await s.browse("owner-1")) == 1
        assert await s.browse(LEGACY_USER_ID) == []
        assert (await s.get_settings("owner-1"))["capture_searches"] is True
        assert await s.rekey_legacy("owner-1") == 0
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_member_cannot_change_global_notification_mutes(client, app):
    event = "app.failed"
    member, _ = await _member(app)
    try:
        r = await member.put(f"/api/notifications/prefs/{event}", json={"muted": True})
        assert r.status_code == 403, r.text
        prefs = (await client.get("/api/notifications/prefs")).json()
        assert next(p for p in prefs if p["event_type"] == event)["muted"] is False
        r = await client.put(f"/api/notifications/prefs/{event}", json={"muted": True})
        assert r.status_code == 200
    finally:
        await member.aclose()
