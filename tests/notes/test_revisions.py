"""Tests for the notes revision / Time Machine layer."""
from __future__ import annotations

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.notes.shared_docs_store import CHECKPOINT_EVERY, SharedDocsStore


# ------------------------------------------------------------------ fixtures

@pytest_asyncio.fixture
async def store(tmp_path):
    s = SharedDocsStore(tmp_path / "shared_docs.db")
    await s.init()
    yield s
    await s.close()


def _make_config(tmp_path):
    return {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }


@pytest_asyncio.fixture
async def client(tmp_path):
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
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
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    shared_docs_store = SharedDocsStore(tmp_path / "shared_docs.db")
    await shared_docs_store.init()
    app.state.shared_docs_store = shared_docs_store
    auth = app.state.auth
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


# ---------------------------------------------------------------- store tests

@pytest.mark.asyncio
async def test_add_entry_creates_create_revision(store):
    doc = await store.create_doc("user1", "note", "Test")
    entry = await store.add_entry(doc["id"], "hello", author="user1")
    revs = await store.list_revisions(entry["id"])
    assert len(revs) == 1
    r = revs[0]
    assert r["rev_index"] == 0
    assert r["op"] == "create"
    assert r["editor_id"] == "user1"
    assert r["editor_type"] == "user"


@pytest.mark.asyncio
async def test_edit_entry_increments_rev_index(store):
    doc = await store.create_doc("user1", "note", "Test")
    entry = await store.add_entry(doc["id"], "v0", author="user1")
    entry_id = entry["id"]

    await store.edit_entry(entry_id, "v1", editor_id="user1")
    await store.edit_entry(entry_id, "v2", editor_id="user1")

    revs = await store.list_revisions(entry_id)
    assert len(revs) == 3
    assert [r["rev_index"] for r in revs] == [0, 1, 2]
    assert revs[0]["op"] == "create"
    assert revs[1]["op"] == "edit"
    assert revs[2]["op"] == "edit"
    assert revs[1]["editor_id"] == "user1"


@pytest.mark.asyncio
async def test_edit_entry_editor_type_agent(store):
    doc = await store.create_doc("user1", "list", "Tasks")
    entry = await store.add_entry(doc["id"], "do it", author="user1")
    await store.edit_entry(entry["id"], "done!", editor_id="atlas", editor_type="agent")
    revs = await store.list_revisions(entry["id"])
    assert revs[1]["editor_type"] == "agent"
    assert revs[1]["editor_id"] == "atlas"


@pytest.mark.asyncio
async def test_entry_text_at_simple(store):
    doc = await store.create_doc("u", "note", "N")
    entry = await store.add_entry(doc["id"], "original", author="u")
    entry_id = entry["id"]
    await store.edit_entry(entry_id, "changed", editor_id="u")
    await store.edit_entry(entry_id, "changed again", editor_id="u")

    assert await store.entry_text_at(entry_id, 0) == "original"
    assert await store.entry_text_at(entry_id, 1) == "changed"
    assert await store.entry_text_at(entry_id, 2) == "changed again"


@pytest.mark.asyncio
async def test_entry_text_at_cross_checkpoint(store):
    """Make >CHECKPOINT_EVERY edits; verify reconstruction crosses a checkpoint."""
    doc = await store.create_doc("u", "note", "Chk")
    entry = await store.add_entry(doc["id"], "text-0", author="u")
    entry_id = entry["id"]

    # Keep a ground-truth list of every text state.
    states = ["text-0"]
    total_edits = CHECKPOINT_EVERY + 5  # 25 edits, crosses one checkpoint at rev 20
    for i in range(1, total_edits + 1):
        new_text = f"text-{i} with some extra words to make the diff interesting"
        await store.edit_entry(entry_id, new_text, editor_id="u")
        states.append(new_text)

    # Spot-check a range of revisions including the create, just before the
    # checkpoint, the checkpoint itself, and after the checkpoint.
    revs = await store.list_revisions(entry_id)
    assert len(revs) == total_edits + 1  # create + edits

    for rev_idx in [0, CHECKPOINT_EVERY - 1, CHECKPOINT_EVERY, CHECKPOINT_EVERY + 1, total_edits]:
        reconstructed = await store.entry_text_at(entry_id, rev_idx)
        assert reconstructed == states[rev_idx], (
            f"Mismatch at rev_index={rev_idx}: expected {states[rev_idx]!r}, "
            f"got {reconstructed!r}"
        )


@pytest.mark.asyncio
async def test_revision_diff_returns_none_for_create(store):
    doc = await store.create_doc("u", "note", "N")
    entry = await store.add_entry(doc["id"], "start", author="u")
    diff = await store.revision_diff(entry["id"], 0)
    assert diff is None


@pytest.mark.asyncio
async def test_revision_diff_set_on_edit(store):
    doc = await store.create_doc("u", "note", "N")
    entry = await store.add_entry(doc["id"], "before", author="u")
    await store.edit_entry(entry["id"], "after", editor_id="u")
    diff = await store.revision_diff(entry["id"], 1)
    assert diff is not None
    assert len(diff) > 0


# ----------------------------------------------------------------- route tests

@pytest.mark.asyncio
async def test_edit_text_route_returns_updated_entry(client):
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "T"})).json()
    entry = (await client.post(f"/api/notes/{doc['id']}/entries", json={"text": "old"})).json()
    resp = await client.patch(
        f"/api/notes/{doc['id']}/entries/{entry['id']}/text",
        json={"text": "new"},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "new"


@pytest.mark.asyncio
async def test_history_route_returns_revision_list(client):
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "T"})).json()
    entry = (await client.post(f"/api/notes/{doc['id']}/entries", json={"text": "v0"})).json()
    await client.patch(
        f"/api/notes/{doc['id']}/entries/{entry['id']}/text",
        json={"text": "v1"},
    )
    resp = await client.get(f"/api/notes/{doc['id']}/entries/{entry['id']}/history")
    assert resp.status_code == 200
    revs = resp.json()
    assert len(revs) == 2
    assert revs[0]["op"] == "create"
    assert revs[1]["op"] == "edit"


@pytest.mark.asyncio
async def test_at_revision_route_reconstructs_text(client):
    doc = (await client.post("/api/notes", json={"kind": "note", "title": "T"})).json()
    entry = (await client.post(f"/api/notes/{doc['id']}/entries", json={"text": "alpha"})).json()
    entry_id = entry["id"]
    doc_id = doc["id"]

    await client.patch(f"/api/notes/{doc_id}/entries/{entry_id}/text", json={"text": "beta"})
    await client.patch(f"/api/notes/{doc_id}/entries/{entry_id}/text", json={"text": "gamma"})

    r0 = (await client.get(f"/api/notes/{doc_id}/entries/{entry_id}/at/0")).json()
    assert r0["text"] == "alpha"

    r1 = (await client.get(f"/api/notes/{doc_id}/entries/{entry_id}/at/1")).json()
    assert r1["text"] == "beta"

    r2 = (await client.get(f"/api/notes/{doc_id}/entries/{entry_id}/at/2")).json()
    assert r2["text"] == "gamma"


@pytest.mark.asyncio
async def test_edit_text_route_owner_only(two_user_clients):
    alice, bob, _ = two_user_clients
    doc = (await alice.post("/api/notes", json={"kind": "note", "title": "A"})).json()
    entry = (await alice.post(f"/api/notes/{doc['id']}/entries", json={"text": "orig"})).json()

    resp = await bob.patch(
        f"/api/notes/{doc['id']}/entries/{entry['id']}/text",
        json={"text": "hacked"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_history_route_readable_by_shared_member(two_user_clients):
    alice, bob, app = two_user_clients
    bob_id = app.state.auth.find_user("bob")["id"]

    doc = (await alice.post("/api/notes", json={"kind": "note", "title": "Shared"})).json()
    entry = (await alice.post(f"/api/notes/{doc['id']}/entries", json={"text": "hi"})).json()

    # Before sharing, bob gets 403.
    assert (await bob.get(f"/api/notes/{doc['id']}/entries/{entry['id']}/history")).status_code == 403

    # Alice shares with bob.
    await alice.post(
        f"/api/notes/{doc['id']}/members",
        json={"member_type": "user", "member_id": bob_id},
    )

    # Now bob can read history.
    resp = await bob.get(f"/api/notes/{doc['id']}/entries/{entry['id']}/history")
    assert resp.status_code == 200
