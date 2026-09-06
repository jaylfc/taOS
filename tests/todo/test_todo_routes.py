"""Tests for /api/todo routes -- CRUD, ownership, items, and reorder."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.todo.todo_store import TodoStore


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
    """Async HTTP client backed by a fresh taOS app with the todo store init'd."""
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    todo_store = TodoStore(tmp_path / "todo.db")
    await todo_store.init()
    app.state.todo_store = todo_store

    app.state.auth.setup_user("admin", "Admin", "", "adminpass")
    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True

    csrf_token = secrets.token_hex(32)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    ) as c:
        yield c

    await todo_store.close()


@pytest_asyncio.fixture
async def two_user_clients(tmp_path):
    """Returns (client_alice, client_bob, app) for ownership tests."""
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)
    todo_store = TodoStore(tmp_path / "todo.db")
    await todo_store.init()
    app.state.todo_store = todo_store

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

    alice_csrf = secrets.token_hex(32)
    bob_csrf = secrets.token_hex(32)

    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": alice_token, "csrf_token": alice_csrf},
        headers={"X-CSRF-Token": alice_csrf},
    ) as alice, AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": bob_token, "csrf_token": bob_csrf},
        headers={"X-CSRF-Token": bob_csrf},
    ) as bob:
        yield alice, bob, app

    await todo_store.close()


# ----------------------------------------------------------------- list tests

@pytest.mark.asyncio
async def test_list_lists_empty(client):
    resp = await client.get("/api/todo")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_and_get_list(client):
    resp = await client.post("/api/todo", json={"title": "Shopping"})
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["title"] == "Shopping"
    assert "items" in doc
    assert doc["items"] == []
    list_id = doc["id"]

    resp2 = await client.get(f"/api/todo/{list_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == list_id


@pytest.mark.asyncio
async def test_patch_list_title_and_archive(client):
    doc = (await client.post("/api/todo", json={"title": "Old"})).json()
    list_id = doc["id"]

    # Rename.
    resp = await client.patch(f"/api/todo/{list_id}", json={"title": "New"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New"

    # Archive.
    resp = await client.patch(f"/api/todo/{list_id}", json={"archived": True})
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None

    # Archived list excluded from default listing.
    listing = (await client.get("/api/todo")).json()
    assert not any(d["id"] == list_id for d in listing)
    listing_all = (await client.get("/api/todo?include_archived=true")).json()
    assert any(d["id"] == list_id for d in listing_all)


# ---------------------------------------------------------------- item tests

@pytest.mark.asyncio
async def test_add_and_list_items(client):
    doc = (await client.post("/api/todo", json={"title": "Todo"})).json()
    list_id = doc["id"]

    item = (await client.post(
        f"/api/todo/{list_id}/items", json={"text": "Buy milk"}
    )).json()
    assert item["text"] == "Buy milk"
    assert not item["done"]
    assert item["position"] == 0

    item2 = (await client.post(
        f"/api/todo/{list_id}/items", json={"text": "Walk dog"}
    )).json()
    assert item2["position"] == 1

    full = (await client.get(f"/api/todo/{list_id}")).json()
    assert len(full["items"]) == 2
    assert full["items"][0]["text"] == "Buy milk"
    assert full["items"][1]["text"] == "Walk dog"


@pytest.mark.asyncio
async def test_patch_item_toggle_done(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    item = (await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": "task"}
    )).json()
    item_id = item["id"]

    resp = await client.patch(
        f"/api/todo/{doc['id']}/items/{item_id}", json={"done": True}
    )
    assert resp.status_code == 200
    assert resp.json()["done"] is True

    resp2 = await client.patch(
        f"/api/todo/{doc['id']}/items/{item_id}", json={"done": False}
    )
    assert resp2.status_code == 200
    assert resp2.json()["done"] is False


@pytest.mark.asyncio
async def test_patch_item_text(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    item = (await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": "old"}
    )).json()

    resp = await client.patch(
        f"/api/todo/{doc['id']}/items/{item['id']}", json={"text": "new text"}
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "new text"


@pytest.mark.asyncio
async def test_add_item_rejects_whitespace_only_text(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    resp = await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": "   "}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_add_item_rejects_empty_text(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    resp = await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": ""}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_add_item_strips_whitespace(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    item = (await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": "  buy milk  "}
    )).json()
    assert item["text"] == "buy milk"


@pytest.mark.asyncio
async def test_patch_item_rejects_whitespace_only_text(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    item = (await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": "task"}
    )).json()
    resp = await client.patch(
        f"/api/todo/{doc['id']}/items/{item['id']}", json={"text": "   "}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_item(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    item = (await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": "delete me"}
    )).json()

    resp = await client.delete(f"/api/todo/{doc['id']}/items/{item['id']}")
    assert resp.status_code == 200

    full = (await client.get(f"/api/todo/{doc['id']}")).json()
    assert not any(e["id"] == item["id"] for e in full["items"])


@pytest.mark.asyncio
async def test_reorder_items(client):
    doc = (await client.post("/api/todo", json={"title": "Order"})).json()
    list_id = doc["id"]

    a = (await client.post(
        f"/api/todo/{list_id}/items", json={"text": "A"}
    )).json()
    b = (await client.post(
        f"/api/todo/{list_id}/items", json={"text": "B"}
    )).json()
    c = (await client.post(
        f"/api/todo/{list_id}/items", json={"text": "C"}
    )).json()

    # Reverse order: C=0, B=1, A=2
    resp = await client.put(
        f"/api/todo/{list_id}/items/reorder",
        json={
            "items": [
                {"id": c["id"], "position": 0},
                {"id": b["id"], "position": 1},
                {"id": a["id"], "position": 2},
            ]
        },
    )
    assert resp.status_code == 200

    full = (await client.get(f"/api/todo/{list_id}")).json()
    items = full["items"]
    assert items[0]["id"] == c["id"]
    assert items[1]["id"] == b["id"]
    assert items[2]["id"] == a["id"]


# ------------------------------------------------------------- ownership tests

@pytest.mark.asyncio
async def test_other_user_cannot_access_list(two_user_clients):
    alice, bob, _ = two_user_clients

    doc = (await alice.post("/api/todo", json={"title": "Alice's list"})).json()
    list_id = doc["id"]

    resp = await bob.get(f"/api/todo/{list_id}")
    assert resp.status_code == 403

    resp = await bob.patch(f"/api/todo/{list_id}", json={"title": "Hacked"})
    assert resp.status_code == 403

    resp = await bob.post(
        f"/api/todo/{list_id}/items", json={"text": "Intruder"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_owner_has_full_access(client):
    doc = (await client.post("/api/todo", json={"title": "Mine"})).json()
    list_id = doc["id"]
    item = (await client.post(
        f"/api/todo/{list_id}/items", json={"text": "item"}
    )).json()

    assert (await client.patch(
        f"/api/todo/{list_id}/items/{item['id']}", json={"text": "updated"}
    )).status_code == 200
    assert (await client.patch(
        f"/api/todo/{list_id}/items/{item['id']}", json={"done": True}
    )).status_code == 200
    assert (await client.delete(
        f"/api/todo/{list_id}/items/{item['id']}"
    )).status_code == 200


# ----------------------------------------------------------- due date tests

@pytest.mark.asyncio
async def test_add_item_with_due_date(client):
    doc = (await client.post("/api/todo", json={"title": "Deadlines"})).json()
    item = (await client.post(
        f"/api/todo/{doc['id']}/items",
        json={"text": "Submit report", "due_at": "2026-12-31T23:59:59+00:00"},
    )).json()
    assert item["due_at"] is not None
    assert item["text"] == "Submit report"


@pytest.mark.asyncio
async def test_add_item_with_invalid_due_date_returns_400(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    resp = await client.post(
        f"/api/todo/{doc['id']}/items",
        json={"text": "x", "due_at": "not-a-date"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_add_item_with_naive_due_date_returns_400(client):
    """Offsetless (naive) datetime strings are rejected."""
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    resp = await client.post(
        f"/api/todo/{doc['id']}/items",
        json={"text": "x", "due_at": "2026-12-31T23:59:59"},
    )
    assert resp.status_code == 400
    assert "timezone" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_patch_item_due_date(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    item = (await client.post(
        f"/api/todo/{doc['id']}/items", json={"text": "task"}
    )).json()

    resp = await client.patch(
        f"/api/todo/{doc['id']}/items/{item['id']}",
        json={"due_at": "2026-07-04T12:00:00+00:00"},
    )
    assert resp.status_code == 200
    assert resp.json()["due_at"] is not None


# ------------------------------------------------------------ not-found tests

@pytest.mark.asyncio
async def test_get_nonexistent_list_returns_404(client):
    resp = await client.get("/api/todo/tl-nonexist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_nonexistent_list_returns_404(client):
    resp = await client.patch("/api/todo/tl-nonexist", json={"title": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_item_to_nonexistent_list_returns_404(client):
    resp = await client.post(
        "/api/todo/tl-nonexist/items", json={"text": "x"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_nonexistent_item_returns_404(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    resp = await client.patch(
        f"/api/todo/{doc['id']}/items/ti-nonexist", json={"done": True}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_item_returns_404(client):
    doc = (await client.post("/api/todo", json={"title": "x"})).json()
    resp = await client.delete(f"/api/todo/{doc['id']}/items/ti-nonexist")
    assert resp.status_code == 404
