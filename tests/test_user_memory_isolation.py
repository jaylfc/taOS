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
async def test_each_person_changes_only_their_own_settings(client, app, store):
    member, _ = await _member(app)
    try:
        r = await member.put("/api/user-memory/settings", json={"capture_searches": True})
        assert r.status_code == 200, r.text
        assert (await member.get("/api/user-memory/settings")).json()["capture_searches"] is True
        # The owner's setting is untouched, and the owner's change does not reach the member.
        assert (await client.get("/api/user-memory/settings")).json()["capture_searches"] is False
        await client.put("/api/user-memory/settings", json={"capture_conversations": False})
        assert (await member.get("/api/user-memory/settings")).json()["capture_conversations"] is True
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_install_default_is_admin_only_and_people_keep_their_choice(client, app, store):
    member, _ = await _member(app)
    try:
        r = await member.put("/api/user-memory/settings/default", json={"capture_searches": True})
        assert r.status_code == 403, r.text
        assert (await client.get("/api/user-memory/settings")).json()["capture_searches"] is False
        await member.put("/api/user-memory/settings", json={"capture_files": False})
        r = await client.put("/api/user-memory/settings/default", json={"capture_searches": True, "capture_files": True})
        assert r.status_code == 200
        got = (await member.get("/api/user-memory/settings")).json()
        assert got["capture_searches"] is True      # inherited from the new default
        assert got["capture_files"] is False         # their own choice wins
    finally:
        await member.aclose()


async def _agent_env(app, store, *, can_read=True):
    owner_id = app.state.auth.get_primary_user()["id"]
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    row = await reg.register(framework="none", display_name="Helper", user_id=owner_id)
    app.state.config.agents.append({
        "name": "helper", "registry_canonical_id": row["canonical_id"],
        "can_read_user_memory": can_read,
    })
    token = app.state.auth.mint_agent_local_token("helper")
    c = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"}, event_hooks=csrf_event_hooks(),
    )
    return owner_id, reg, c


@pytest.mark.asyncio
async def test_agent_search_answers_only_the_agents_own_token_for_its_owner(client, app, store):
    owner_id, reg, agent = await _agent_env(app, store)
    try:
        await store.save_chunk(owner_id, "pineapple notes", "n", "notes")
        member, _ = await _member(app)
        try:
            # A member (or even the owner) naming the agent is refused: the agent's own token is required.
            assert (await member.get("/api/user-memory/agent-search?q=pineapple&agent_name=helper")).status_code == 403
            assert (await client.get("/api/user-memory/agent-search?q=pineapple&agent_name=helper")).status_code == 403
        finally:
            await member.aclose()
        ok = await agent.get("/api/user-memory/agent-search?q=pineapple&agent_name=helper")
        assert ok.status_code == 200 and len(ok.json()["results"]) == 1
        other = await agent.get("/api/user-memory/agent-search?q=pineapple&agent_name=someone-else")
        assert other.status_code == 403
    finally:
        await agent.aclose()
        await reg.close()


@pytest.mark.asyncio
async def test_agent_search_refused_without_the_permission_flag(client, app, store):
    owner_id, reg, agent = await _agent_env(app, store, can_read=False)
    try:
        await store.save_chunk(owner_id, "pineapple notes", "n", "notes")
        r = await agent.get("/api/user-memory/agent-search?q=pineapple&agent_name=helper")
        assert r.status_code == 403
    finally:
        await agent.aclose()
        await reg.close()


@pytest.mark.asyncio
async def test_agent_token_cannot_use_the_person_routes(client, app, store):
    owner_id, reg, agent = await _agent_env(app, store, can_read=False)
    try:
        h = await store.save_chunk(owner_id, "private note", "n", "notes")
        assert (await agent.get("/api/user-memory/browse")).status_code == 403
        assert (await agent.get("/api/user-memory/search?q=private")).status_code == 403
        assert (await agent.get("/api/user-memory/stats")).status_code == 403
        assert (await agent.post("/api/user-memory/save", json={"content": "x"})).status_code == 403
        assert (await agent.delete(f"/api/user-memory/chunk/{h}")).status_code == 403
        assert len(await store.browse(owner_id)) == 1
    finally:
        await agent.aclose()
        await reg.close()


@pytest.mark.asyncio
async def test_legacy_shared_rows_move_to_the_owner_and_are_idempotent(tmp_path):
    from tinyagentos.user_memory import LEGACY_USER_ID, UserMemoryStore

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
