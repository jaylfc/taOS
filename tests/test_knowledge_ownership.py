"""Tests for per-user ownership scoping of the knowledge store.

Covers:
- Store: add_item stores user_id; list_for_user returns only that user's items
- Store: search_fts scoped by user_id returns only that user's items
- Store: get_item with user_id filter hides other users' items
- Routes: create binds caller's user_id
- Routes: member keyword search returns only own items
- Routes: member semantic search (mocked qmd) returns only own items
- Routes: member get_item returns 404 for other user's item
- Routes: member delete returns 403 for other user's item
- Routes: admin sees all items
- Routes: legacy rows (user_id='') visible to admin, hidden from members
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.auth import get_current_user
from tinyagentos.knowledge_store import KnowledgeStore


# ---------------------------------------------------------------------------
# Store-level tests (no HTTP, direct method calls)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "knowledge-media")
    await s.init()
    yield s
    await s.close()


async def _add(store: KnowledgeStore, title: str, user_id: str = "") -> str:
    return await store.add_item(
        source_type="article",
        source_url=f"https://example.com/{title.lower().replace(' ', '-')}",
        title=title,
        author="tester",
        content=f"content for {title}",
        summary="",
        categories=[],
        tags=[],
        metadata={},
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_add_item_stores_user_id(store):
    item_id = await _add(store, "Alice Article", user_id="user-alice")
    item = await store.get_item(item_id)
    assert item is not None
    assert item["user_id"] == "user-alice"


@pytest.mark.asyncio
async def test_list_for_user_returns_only_own(store):
    await _add(store, "Alice Post", user_id="user-alice")
    await _add(store, "Bob Post", user_id="user-bob")
    await _add(store, "Legacy Post", user_id="")  # legacy row

    alice_items = await store.list_for_user("user-alice")
    assert len(alice_items) == 1
    assert alice_items[0]["title"] == "Alice Post"

    bob_items = await store.list_for_user("user-bob")
    assert len(bob_items) == 1
    assert bob_items[0]["title"] == "Bob Post"


@pytest.mark.asyncio
async def test_list_items_no_filter_returns_all(store):
    """No user_id filter (admin path) returns all rows."""
    await _add(store, "A", user_id="user-1")
    await _add(store, "B", user_id="user-2")
    await _add(store, "C", user_id="")
    all_items = await store.list_items()
    assert len(all_items) == 3


@pytest.mark.asyncio
async def test_get_item_with_user_id_filter_hides_other_owner(store):
    item_id = await _add(store, "Bob Secret", user_id="user-bob")
    # Alice cannot see it
    result = await store.get_item(item_id, user_id="user-alice")
    assert result is None
    # Bob can see it
    result = await store.get_item(item_id, user_id="user-bob")
    assert result is not None
    assert result["title"] == "Bob Secret"
    # No filter (admin) can see it
    result = await store.get_item(item_id)
    assert result is not None


@pytest.mark.asyncio
async def test_search_fts_scoped_by_user_id(store):
    await _add(store, "Asyncio Guide", user_id="user-alice")
    await store.add_item(
        source_type="article",
        source_url="https://example.com/asyncio-bob",
        title="Asyncio Bob",
        author="dev",
        content="asyncio event loop bob",
        summary="",
        categories=[],
        tags=[],
        metadata={},
        user_id="user-bob",
    )

    alice_results = await store.search_fts("asyncio", user_id="user-alice")
    assert all(r["user_id"] == "user-alice" for r in alice_results)
    assert len(alice_results) == 1

    bob_results = await store.search_fts("asyncio", user_id="user-bob")
    assert len(bob_results) == 1
    assert bob_results[0]["user_id"] == "user-bob"

    # Admin (no filter) sees both
    all_results = await store.search_fts("asyncio")
    assert len(all_results) == 2


@pytest.mark.asyncio
async def test_legacy_rows_hidden_from_member_search(store):
    """Legacy rows (user_id='') are not returned when filtering by a user_id."""
    await _add(store, "Old Article asyncio", user_id="")
    results = await store.search_fts("asyncio", user_id="user-alice")
    assert len(results) == 0
    # Admin (no filter) sees it
    results_admin = await store.search_fts("asyncio")
    assert len(results_admin) == 1


# ---------------------------------------------------------------------------
# Route-level tests
# ---------------------------------------------------------------------------

def _make_app(tmp_path: Path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return create_app(data_dir=tmp_path)


async def _init_app_stores(app):
    await app.state.metrics.init()
    await app.state.notifications.init()
    await app.state.qmd_client.init()
    await app.state.knowledge_store.init()
    app.state._startup_complete = True


async def _teardown_app_stores(app):
    await app.state.knowledge_store.close()
    await app.state.notifications.close()
    await app.state.metrics.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()


def _make_client(app, token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


@pytest_asyncio.fixture
async def two_user_app(tmp_path):
    """App with an admin user and a member user, plus their session tokens."""
    app = _make_app(tmp_path)
    await _init_app_stores(app)

    auth = app.state.auth
    # Admin user
    auth.setup_user("admin", "Test Admin", "", "testpass")
    admin_record = auth.find_user("admin")
    admin_uid = admin_record["id"]
    admin_token = auth.create_session(user_id=admin_uid, long_lived=True)

    # Member user (via invite flow)
    invite_code = auth.add_user_invite("member", invited_by_username="admin")
    auth.complete_invite("member", invite_code, "Test Member", "", "testpass2")
    member_record = auth.find_user("member")
    member_uid = member_record["id"]
    member_token = auth.create_session(user_id=member_uid, long_lived=True)

    yield app, admin_uid, admin_token, member_uid, member_token

    await _teardown_app_stores(app)


@pytest.mark.asyncio
async def test_ingest_binds_caller_user_id(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    async with _make_client(app, member_token) as client:
        resp = await client.post("/api/knowledge/ingest", json={
            "url": "https://example.com/member-article",
            "title": "Member Article",
            "text": "Some content.",
            "categories": [],
            "source": "test",
        })
    assert resp.status_code == 200
    item_id = resp.json()["id"]
    # Verify item was stored with member's user_id
    item = await app.state.knowledge_store.get_item(item_id)
    assert item is not None
    assert item["user_id"] == member_uid


@pytest.mark.asyncio
async def test_member_list_returns_only_own_items(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store

    # Seed one item per user and one legacy row
    await _add(store, "Admin Item", user_id=admin_uid)
    await _add(store, "Member Item", user_id=member_uid)
    await _add(store, "Legacy Item", user_id="")

    async with _make_client(app, member_token) as client:
        resp = await client.get("/api/knowledge/items")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Member Item"


@pytest.mark.asyncio
async def test_admin_list_returns_all_items(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store

    await _add(store, "Admin Item", user_id=admin_uid)
    await _add(store, "Member Item", user_id=member_uid)
    await _add(store, "Legacy Item", user_id="")

    async with _make_client(app, admin_token) as client:
        resp = await client.get("/api/knowledge/items")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 3


@pytest.mark.asyncio
async def test_legacy_rows_hidden_from_member_list(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store
    await _add(store, "Old Row", user_id="")

    async with _make_client(app, member_token) as client:
        resp = await client.get("/api/knowledge/items")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_member_get_own_item_ok(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store
    item_id = await _add(store, "Mine", user_id=member_uid)

    async with _make_client(app, member_token) as client:
        resp = await client.get(f"/api/knowledge/items/{item_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == item_id


@pytest.mark.asyncio
async def test_member_get_other_item_404(two_user_app):
    """Non-owner receives 404 (existence-hiding)."""
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store
    item_id = await _add(store, "Not Mine", user_id=admin_uid)

    async with _make_client(app, member_token) as client:
        resp = await client.get(f"/api/knowledge/items/{item_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_get_any_item_ok(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store
    item_id = await _add(store, "Member Item", user_id=member_uid)

    async with _make_client(app, admin_token) as client:
        resp = await client.get(f"/api/knowledge/items/{item_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_member_delete_own_item_ok(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store
    item_id = await _add(store, "Delete Me", user_id=member_uid)

    async with _make_client(app, member_token) as client:
        resp = await client.delete(f"/api/knowledge/items/{item_id}")
    assert resp.status_code == 200
    assert await store.get_item(item_id) is None


@pytest.mark.asyncio
async def test_member_delete_other_item_403(two_user_app):
    """Non-owner gets 403 when trying to delete another user's item."""
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store
    item_id = await _add(store, "Admin Only Item", user_id=admin_uid)

    async with _make_client(app, member_token) as client:
        resp = await client.delete(f"/api/knowledge/items/{item_id}")
    assert resp.status_code == 403
    # Item must still exist
    assert await store.get_item(item_id) is not None


@pytest.mark.asyncio
async def test_member_keyword_search_returns_only_own(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store

    await store.add_item(
        source_type="article",
        source_url="https://example.com/admin-asyncio",
        title="Admin Asyncio",
        author="admin",
        content="asyncio stuff",
        summary="",
        categories=[],
        tags=[],
        metadata={},
        user_id=admin_uid,
    )
    await store.add_item(
        source_type="article",
        source_url="https://example.com/member-asyncio",
        title="Member Asyncio",
        author="member",
        content="asyncio stuff",
        summary="",
        categories=[],
        tags=[],
        metadata={},
        user_id=member_uid,
    )

    async with _make_client(app, member_token) as client:
        resp = await client.get("/api/knowledge/search?q=asyncio&mode=keyword")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["user_id"] == member_uid


@pytest.mark.asyncio
async def test_admin_keyword_search_returns_all(two_user_app):
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store

    for uid in [admin_uid, member_uid, ""]:
        await store.add_item(
            source_type="article",
            source_url=f"https://example.com/asyncio-{uid}",
            title=f"Asyncio {uid}",
            author="dev",
            content="asyncio coroutine",
            summary="",
            categories=[],
            tags=[],
            metadata={},
            user_id=uid,
        )

    async with _make_client(app, admin_token) as client:
        resp = await client.get("/api/knowledge/search?q=asyncio&mode=keyword")
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 3


@pytest.mark.asyncio
async def test_member_semantic_search_scoped(two_user_app):
    """Semantic search post-filters qmd results to caller's items."""
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store

    member_id = await _add(store, "Member Vector Article", user_id=member_uid)
    admin_id = await _add(store, "Admin Vector Article", user_id=admin_uid)

    # Mock http_client to return both ids from qmd
    mock_http = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"id": member_id, "score": 0.9},
            {"id": admin_id, "score": 0.8},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    app.state.http_client = mock_http

    async with _make_client(app, member_token) as client:
        resp = await client.get("/api/knowledge/search?q=vector&mode=semantic")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "semantic"
    results = data["results"]
    assert len(results) == 1
    assert results[0]["id"] == member_id


@pytest.mark.asyncio
async def test_admin_semantic_search_returns_all(two_user_app):
    """Admin receives all qmd results unfiltered."""
    app, admin_uid, admin_token, member_uid, member_token = two_user_app
    store = app.state.knowledge_store

    member_id = await _add(store, "M Vector", user_id=member_uid)
    admin_id = await _add(store, "A Vector", user_id=admin_uid)

    mock_http = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"id": member_id, "score": 0.9},
            {"id": admin_id, "score": 0.8},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    app.state.http_client = mock_http

    async with _make_client(app, admin_token) as client:
        resp = await client.get("/api/knowledge/search?q=vector&mode=semantic")

    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 2


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(tmp_path):
    """Unauthenticated requests to knowledge routes return 401."""
    app = _make_app(tmp_path)
    await _init_app_stores(app)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/knowledge/items")
        assert resp.status_code == 401
    finally:
        await _teardown_app_stores(app)
