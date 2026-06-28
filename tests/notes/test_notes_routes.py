"""Tests for /api/notes routes -- CRUD, ownership, permissions, and agent trigger."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.notes.shared_docs_store import SharedDocsStore


# --------------------------------------------------------------------- helpers

def _make_config(tmp_path) -> dict:
    return {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }


@pytest_asyncio.fixture
async def client(tmp_path):
    """Async HTTP client backed by a fresh taOS app with the notes store init'd."""
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    # Init stores that the routes need (bypassing lifespan).
    shared_docs_store = SharedDocsStore(tmp_path / "shared_docs.db")
    await shared_docs_store.init()
    app.state.shared_docs_store = shared_docs_store

    # Auth setup.
    app.state.auth.setup_user("admin", "Admin", "", "adminpass")
    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c

    await shared_docs_store.close()


@pytest_asyncio.fixture
async def two_user_clients(tmp_path):
    """Returns (client_alice, client_bob, app) for ownership tests."""
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)
    shared_docs_store = SharedDocsStore(tmp_path / "shared_docs.db")
    await shared_docs_store.init()
    app.state.shared_docs_store = shared_docs_store

    auth = app.state.auth
    # First user is always admin.
    auth.setup_user("alice", "Alice", "", "alicepass")
    alice_rec = auth.find_user("alice")
    alice_token = auth.create_session(user_id=alice_rec["id"], long_lived=True)

    bob_invite = auth.add_user_invite("bob", "alice")
    auth.complete_invite("bob", bob_invite, "bob", "", "bobpass123")
    bob_rec = auth.find_user("bob")
    bob_token = auth.create_session(user_id=bob_rec["id"], long_lived=True)

    app.state._startup_complete = True
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": alice_token},
    ) as alice, AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": bob_token},
    ) as bob:
        yield alice, bob, app

    await shared_docs_store.close()


# ------------------------------------------------------------------ doc tests

@pytest.mark.asyncio
async def test_list_docs_empty(client):
    resp = await client.get("/api/notes")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_and_get_doc(client):
    resp = await client.post("/api/notes", json={"kind": "note", "title": "App ideas"})
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["title"] == "App ideas"
    assert doc["kind"] == "note"
    doc_id = doc["id"]

    resp2 = await client.get(f"/api/notes/{doc_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == doc_id


@pytest.mark.asyncio
async def test_invalid_kind_returns_400(client):
    resp = await client.post("/api/notes", json={"kind": "spreadsheet", "title": "x"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_doc_title_and_archive(client):
    doc = (await client.post("/api/notes", json={"kind": "list", "title": "Old"})).json()
    doc_id = doc["id"]

    # Rename.
    resp = await client.patch(f"/api/notes/{doc_id}", json={"title": "New"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New"

    # Archive.
    resp = await client.patch(f"/api/notes/{doc_id}", json={"archived": True})
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None

    # Archived doc excluded from default listing.
    listing = (await client.get("/api/notes")).json()
    assert not any(d["id"] == doc_id for d in listing)
    listing_all = (await client.get("/api/notes?include_archived=true")).json()
    assert any(d["id"] == doc_id for d in listing_all)


# ---------------------------------------------------------------- entry tests

@pytest.mark.asyncio
async def test_add_and_list_entries(client):
    doc = (await client.post("/api/notes", json={"kind": "list", "title": "Todo"})).json()
    doc_id = doc["id"]

    entry = (await client.post(f"/api/notes/{doc_id}/entries", json={"text": "Buy milk"})).json()
    assert entry["text"] == "Buy milk"
    assert not entry["done"]

    full = (await client.get(f"/api/notes/{doc_id}")).json()
    assert any(e["text"] == "Buy milk" for e in full["entries"])


@pytest.mark.asyncio
async def test_patch_entry_done(client):
    doc = (await client.post("/api/notes", json={"kind": "list", "title": "x"})).json()
    entry = (await client.post(f"/api/notes/{doc['id']}/entries", json={"text": "task"})).json()
    entry_id = entry["id"]

    resp = await client.patch(
        f"/api/notes/{doc['id']}/entries/{entry_id}", json={"done": True}
    )
    assert resp.status_code == 200

    full = (await client.get(f"/api/notes/{doc['id']}")).json()
    e = next(e for e in full["entries"] if e["id"] == entry_id)
    assert e["done"] is True


@pytest.mark.asyncio
async def test_delete_entry(client):
    doc = (await client.post("/api/notes", json={"kind": "list", "title": "x"})).json()
    entry = (await client.post(f"/api/notes/{doc['id']}/entries", json={"text": "delete me"})).json()
    entry_id = entry["id"]

    resp = await client.delete(f"/api/notes/{doc['id']}/entries/{entry_id}")
    assert resp.status_code == 200

    full = (await client.get(f"/api/notes/{doc['id']}")).json()
    assert not any(e["id"] == entry_id for e in full["entries"])


# ------------------------------------------------------------- ownership tests

@pytest.mark.asyncio
async def test_other_user_cannot_access_doc(two_user_clients):
    alice, bob, _ = two_user_clients

    doc = (await alice.post("/api/notes", json={"kind": "note", "title": "Alice's note"})).json()
    doc_id = doc["id"]

    resp = await bob.get(f"/api/notes/{doc_id}")
    assert resp.status_code == 403

    resp = await bob.patch(f"/api/notes/{doc_id}", json={"title": "Hacked"})
    assert resp.status_code == 403

    resp = await bob.post(f"/api/notes/{doc_id}/entries", json={"text": "Intruder"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_user_member_can_read_shared_doc(two_user_clients):
    alice, bob, app = two_user_clients
    bob_id = app.state.auth.find_user("bob")["id"]

    doc = (await alice.post("/api/notes", json={"kind": "note", "title": "Shared"})).json()
    doc_id = doc["id"]

    # Before sharing, bob cannot read it (consistent with list_docs).
    assert (await bob.get(f"/api/notes/{doc_id}")).status_code == 403

    # Alice shares the doc with bob as a user member.
    r = await alice.post(
        f"/api/notes/{doc_id}/members",
        json={"member_type": "user", "member_id": bob_id},
    )
    assert r.status_code == 200

    # Bob can now read the doc and its members.
    assert (await bob.get(f"/api/notes/{doc_id}")).status_code == 200
    assert (await bob.get(f"/api/notes/{doc_id}/members")).status_code == 200
    # Default permission is 'contributor': can add entries but not edit/delete them.
    assert (await bob.post(f"/api/notes/{doc_id}/entries", json={"text": "hi"})).status_code == 200
    # Contributor cannot edit existing entries or manage the doc.
    entry = (await alice.post(f"/api/notes/{doc_id}/entries", json={"text": "alice entry"})).json()
    eid = entry["id"]
    assert (await bob.patch(f"/api/notes/{doc_id}/entries/{eid}/text", json={"text": "x"})).status_code == 403
    assert (await bob.patch(f"/api/notes/{doc_id}/entries/{eid}", json={"done": True})).status_code == 403
    assert (await bob.delete(f"/api/notes/{doc_id}/entries/{eid}")).status_code == 403
    assert (await bob.patch(f"/api/notes/{doc_id}", json={"title": "x"})).status_code == 403


@pytest.mark.asyncio
async def test_editor_member_can_edit_and_remove_entries(two_user_clients):
    """A member shared with permission='editor' can add, edit, toggle-done, and delete."""
    alice, bob, app = two_user_clients
    bob_id = app.state.auth.find_user("bob")["id"]
    doc = (await alice.post("/api/notes", json={"kind": "list", "title": "Groceries"})).json()
    doc_id = doc["id"]
    # Share with editor permission so bob can edit/delete.
    await alice.post(
        f"/api/notes/{doc_id}/members",
        json={"member_type": "user", "member_id": bob_id, "permission": "editor"},
    )

    entry = (await bob.post(f"/api/notes/{doc_id}/entries", json={"text": "milk"})).json()
    eid = entry["id"]
    assert (await bob.patch(f"/api/notes/{doc_id}/entries/{eid}/text", json={"text": "oat milk"})).status_code == 200
    assert (await bob.patch(f"/api/notes/{doc_id}/entries/{eid}", json={"done": True})).status_code == 200
    assert (await bob.delete(f"/api/notes/{doc_id}/entries/{eid}")).status_code == 200

    # Once unshared, the former member can no longer write.
    await alice.delete(f"/api/notes/{doc_id}/members/user/{bob_id}")
    assert (await bob.post(f"/api/notes/{doc_id}/entries", json={"text": "x"})).status_code == 403


# ------------------------------------------------------------ member tests

@pytest.mark.asyncio
async def test_add_list_remove_member(client):
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "Ideas"})).json()
    doc_id = doc["id"]

    resp = await client.post(
        f"/api/notes/{doc_id}/members",
        json={"member_type": "agent", "member_id": "atlas", "standing_instruction": "Research it."},
    )
    assert resp.status_code == 200
    members = resp.json()
    assert any(
        m["member_type"] == "agent" and m["member_id"] == "atlas" for m in members
    )

    resp = await client.get(f"/api/notes/{doc_id}/members")
    assert resp.status_code == 200
    assert any(m["member_id"] == "atlas" for m in resp.json())

    resp = await client.delete(f"/api/notes/{doc_id}/members/agent/atlas")
    assert resp.status_code == 200

    remaining = (await client.get(f"/api/notes/{doc_id}/members")).json()
    assert not any(m["member_id"] == "atlas" for m in remaining)


@pytest.mark.asyncio
async def test_invalid_member_type_returns_400(client):
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "x"})).json()
    resp = await client.post(
        f"/api/notes/{doc['id']}/members",
        json={"member_type": "robot", "member_id": "r2d2"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_permission_returns_400(client):
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "x"})).json()
    resp = await client.post(
        f"/api/notes/{doc['id']}/members",
        json={"member_type": "user", "member_id": "bob", "permission": "superuser"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_action_returns_400(client):
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "x"})).json()
    resp = await client.post(
        f"/api/notes/{doc['id']}/members",
        json={"member_type": "agent", "member_id": "atlas", "action": "sleep"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_viewer_cannot_add_or_edit(two_user_clients):
    """A viewer member can read but not add, edit, or delete entries."""
    alice, bob, app = two_user_clients
    bob_id = app.state.auth.find_user("bob")["id"]
    doc = (await alice.post("/api/notes", json={"kind": "list", "title": "ViewOnly"})).json()
    doc_id = doc["id"]
    await alice.post(
        f"/api/notes/{doc_id}/members",
        json={"member_type": "user", "member_id": bob_id, "permission": "viewer"},
    )

    assert (await bob.get(f"/api/notes/{doc_id}")).status_code == 200
    assert (await bob.post(f"/api/notes/{doc_id}/entries", json={"text": "try"})).status_code == 403

    entry = (await alice.post(f"/api/notes/{doc_id}/entries", json={"text": "alice entry"})).json()
    eid = entry["id"]
    assert (await bob.patch(f"/api/notes/{doc_id}/entries/{eid}/text", json={"text": "x"})).status_code == 403
    assert (await bob.patch(f"/api/notes/{doc_id}/entries/{eid}", json={"done": True})).status_code == 403
    assert (await bob.delete(f"/api/notes/{doc_id}/entries/{eid}")).status_code == 403


@pytest.mark.asyncio
async def test_contributor_can_add_but_not_edit(two_user_clients):
    """A contributor member can add new entries but cannot edit or delete existing ones."""
    alice, bob, app = two_user_clients
    bob_id = app.state.auth.find_user("bob")["id"]
    doc = (await alice.post("/api/notes", json={"kind": "list", "title": "ContribOnly"})).json()
    doc_id = doc["id"]
    await alice.post(
        f"/api/notes/{doc_id}/members",
        json={"member_type": "user", "member_id": bob_id, "permission": "contributor"},
    )

    assert (await bob.post(f"/api/notes/{doc_id}/entries", json={"text": "new"})).status_code == 200

    entry = (await alice.post(f"/api/notes/{doc_id}/entries", json={"text": "alice entry"})).json()
    eid = entry["id"]
    assert (await bob.patch(f"/api/notes/{doc_id}/entries/{eid}", json={"done": True})).status_code == 403
    assert (await bob.delete(f"/api/notes/{doc_id}/entries/{eid}")).status_code == 403


@pytest.mark.asyncio
async def test_owner_always_has_full_access(two_user_clients):
    """The owner can add, edit, and delete regardless of any member permission rules."""
    alice, bob, _ = two_user_clients
    doc = (await alice.post("/api/notes", json={"kind": "list", "title": "Mine"})).json()
    doc_id = doc["id"]
    entry = (await alice.post(f"/api/notes/{doc_id}/entries", json={"text": "item"})).json()
    eid = entry["id"]
    assert (await alice.patch(f"/api/notes/{doc_id}/entries/{eid}/text", json={"text": "updated"})).status_code == 200
    assert (await alice.patch(f"/api/notes/{doc_id}/entries/{eid}", json={"done": True})).status_code == 200
    assert (await alice.delete(f"/api/notes/{doc_id}/entries/{eid}")).status_code == 200


@pytest.mark.asyncio
async def test_member_default_permission_is_contributor(client):
    """When no permission is passed, the member gets 'contributor' by default."""
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "x"})).json()
    doc_id = doc["id"]
    await client.post(
        f"/api/notes/{doc_id}/members",
        json={"member_type": "agent", "member_id": "atlas"},
    )
    members = (await client.get(f"/api/notes/{doc_id}/members")).json()
    atlas = next(m for m in members if m["member_id"] == "atlas")
    assert atlas["permission"] == "contributor"
    assert atlas["action"] is None


@pytest.mark.asyncio
async def test_add_member_stores_permission_and_action(client):
    """The permission and action fields are persisted and returned via list_members."""
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "x"})).json()
    doc_id = doc["id"]
    await client.post(
        f"/api/notes/{doc_id}/members",
        json={"member_type": "agent", "member_id": "nova", "permission": "editor", "action": "critique"},
    )
    members = (await client.get(f"/api/notes/{doc_id}/members")).json()
    nova = next(m for m in members if m["member_id"] == "nova")
    assert nova["permission"] == "editor"
    assert nova["action"] == "critique"


# ------------------------------------------------- agent tool permission tests

@pytest.mark.asyncio
async def test_agent_tool_viewer_cannot_add(tmp_path):
    """An agent with viewer permission is rejected by execute_notes_add_entry."""
    import yaml
    from types import SimpleNamespace

    from tinyagentos.tools.notes_tools import execute_notes_add_entry

    config_data = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)
    store = SharedDocsStore(tmp_path / "shared_docs.db")
    await store.init()
    app.state.shared_docs_store = store

    doc = await store.create_doc("owner", "note", "Ideas")
    await store.add_member(doc["id"], "agent", "atlas", permission="viewer")

    req = SimpleNamespace(
        app=SimpleNamespace(state=app.state),
        state=SimpleNamespace(agent_name="atlas"),
    )
    result = await execute_notes_add_entry(
        {"agent_name": "atlas", "doc_id": doc["id"], "text": "hi"}, req
    )
    assert "error" in result
    assert "permission" in result["error"]
    await store.close()


@pytest.mark.asyncio
async def test_agent_tool_contributor_can_add(tmp_path):
    """An agent with contributor permission can append via execute_notes_add_entry."""
    import yaml
    from types import SimpleNamespace

    from tinyagentos.tools.notes_tools import execute_notes_add_entry

    config_data = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)
    store = SharedDocsStore(tmp_path / "shared_docs.db")
    await store.init()
    app.state.shared_docs_store = store

    doc = await store.create_doc("owner", "note", "Ideas")
    await store.add_member(doc["id"], "agent", "atlas", permission="contributor")

    req = SimpleNamespace(
        app=SimpleNamespace(state=app.state),
        state=SimpleNamespace(agent_name="atlas"),
    )
    result = await execute_notes_add_entry(
        {"agent_name": "atlas", "doc_id": doc["id"], "text": "a new idea"}, req
    )
    assert result.get("ok") is True
    assert "entry_id" in result
    await store.close()


# ----------------------------------------------------------- agent trigger test

@pytest.mark.asyncio
async def test_add_entry_triggers_agent_dm(tmp_path, monkeypatch):
    """Adding an entry to a doc with an agent member sends a DM to that agent."""
    config_data = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)
    shared_docs_store = SharedDocsStore(tmp_path / "shared_docs.db")
    await shared_docs_store.init()
    app.state.shared_docs_store = shared_docs_store

    auth = app.state.auth
    auth.setup_user("admin", "Admin", "", "adminpass")
    record = auth.find_user("admin")
    token = auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True

    # Plant a fake agent with a chat_channel_id in the config.
    fake_channel_id = "ch-test-abc123"
    app.state.config.agents.append({
        "name": "atlas",
        "host": "localhost",
        "color": "#abc",
        "chat_channel_id": fake_channel_id,
    })

    # Capture calls to message_store.send_message.
    sent: list[dict] = []

    async def _fake_send(channel_id, author_id, author_type, content, **kwargs):
        sent.append({"channel_id": channel_id, "content": content})
        return {}

    fake_msg_store = MagicMock()
    fake_msg_store.send_message = _fake_send
    app.state.chat_messages = fake_msg_store

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        doc = (await c.post("/api/notes", json={"kind": "note", "title": "Ideas"})).json()
        doc_id = doc["id"]

        # Add atlas as an agent member with a standing instruction.
        await c.post(
            f"/api/notes/{doc_id}/members",
            json={
                "member_type": "agent",
                "member_id": "atlas",
                "standing_instruction": "Research it.",
            },
        )

        # Adding an entry should trigger the DM.
        resp = await c.post(f"/api/notes/{doc_id}/entries", json={"text": "Build a todo app"})
        assert resp.status_code == 200

    assert len(sent) == 1
    assert sent[0]["channel_id"] == fake_channel_id
    assert "Research it." in sent[0]["content"]
    assert "Build a todo app" in sent[0]["content"]

    await shared_docs_store.close()


@pytest.mark.asyncio
async def test_add_entry_no_agent_members_no_send(tmp_path):
    """When there are no agent members, no message is sent."""
    config_data = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)
    shared_docs_store = SharedDocsStore(tmp_path / "shared_docs.db")
    await shared_docs_store.init()
    app.state.shared_docs_store = shared_docs_store

    auth = app.state.auth
    auth.setup_user("admin", "Admin", "", "adminpass")
    record = auth.find_user("admin")
    token = auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True

    sent: list[dict] = []

    async def _fake_send(channel_id, author_id, author_type, content, **kwargs):
        sent.append({})
        return {}

    fake_msg_store = MagicMock()
    fake_msg_store.send_message = _fake_send
    app.state.chat_messages = fake_msg_store

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        doc = (await c.post("/api/notes", json={"kind": "note", "title": "Solo"})).json()
        await c.post(f"/api/notes/{doc['id']}/entries", json={"text": "Just me"})

    assert sent == [], "no message should be sent when there are no agent members"
    await shared_docs_store.close()


@pytest.mark.asyncio
async def test_trigger_message_reflects_action(tmp_path):
    """When an agent member has an action, the trigger message contains the directive."""
    import yaml
    from types import SimpleNamespace

    from tinyagentos.routes.notes import _trigger_agent_notifications

    config_data = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))
    app = create_app(data_dir=tmp_path)
    store = SharedDocsStore(tmp_path / "shared_docs.db")
    await store.init()
    app.state.shared_docs_store = store

    doc = await store.create_doc("owner", "note", "Plans")
    await store.add_member(doc["id"], "agent", "atlas", action="plan")
    app.state.config.agents.append({"name": "atlas", "chat_channel_id": "ch-atlas"})

    sent: list[dict] = []

    async def _fake_send(channel_id, author_id, author_type, content, **kwargs):
        sent.append({"channel_id": channel_id, "content": content})
        return {}

    msg = MagicMock()
    msg.send_message = _fake_send
    app.state.chat_messages = msg

    req = SimpleNamespace(app=SimpleNamespace(state=app.state))
    await _trigger_agent_notifications(req, doc, "build a login page")

    assert len(sent) == 1
    assert "Plan this:" in sent[0]["content"]
    assert "build a login page" in sent[0]["content"]
    await store.close()


@pytest.mark.asyncio
async def test_trigger_skips_authoring_agent(tmp_path):
    """Loop guard: skip_agent is not notified about its own write, others are."""
    from types import SimpleNamespace

    from tinyagentos.routes.notes import _trigger_agent_notifications

    config_data = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))
    app = create_app(data_dir=tmp_path)
    store = SharedDocsStore(tmp_path / "shared_docs.db")
    await store.init()
    app.state.shared_docs_store = store

    doc = await store.create_doc("owner", "note", "Ideas")
    await store.add_member(doc["id"], "agent", "atlas", "research")
    await store.add_member(doc["id"], "agent", "nova", "critique")
    app.state.config.agents.append({"name": "atlas", "chat_channel_id": "ch-atlas"})
    app.state.config.agents.append({"name": "nova", "chat_channel_id": "ch-nova"})

    sent: list[str] = []

    async def _fake_send(channel_id, author_id, author_type, content, **kwargs):
        sent.append(channel_id)
        return {}

    msg = MagicMock()
    msg.send_message = _fake_send
    app.state.chat_messages = msg

    req = SimpleNamespace(app=SimpleNamespace(state=app.state))
    await _trigger_agent_notifications(req, doc, "a new idea", skip_agent="atlas")

    assert "ch-nova" in sent
    assert "ch-atlas" not in sent
    await store.close()
