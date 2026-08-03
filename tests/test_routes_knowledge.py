"""Endpoint tests for tinyagentos/routes/knowledge.py.

Mirrors tests/test_routes_activity.py: every test uses the authenticated
async ``client`` fixture from conftest (valid session cookie, CSRF bypassed in
tests via the autouse conftest patch) and asserts on the real HTTP status code
plus the JSON shape the route handler returns.

The backing stores on ``app.state`` are swapped for AsyncMock instances so the
route handlers (dependency injection, auth scoping, ownership checks, error
handling, Pydantic body validation) are exercised deterministically and without
any network or background-task side effects.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.auth import get_current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(client, monkeypatch, **returns):
    """Attach a fresh AsyncMock as app.state.knowledge_store.

    ``returns`` maps method names to their awaited return values, e.g.
    ``_store(client, monkeypatch, list_items=[])``.
    """
    store = AsyncMock()
    for name, value in returns.items():
        getattr(store, name).return_value = value
    monkeypatch.setattr(client._transport.app.state, "knowledge_store", store)
    return store


def _pipeline(client, monkeypatch, item_id="new-item-id"):
    """Attach a fresh AsyncMock as app.state.ingest_pipeline."""
    pipeline = AsyncMock()
    pipeline.submit_background = AsyncMock(return_value=item_id)
    monkeypatch.setattr(client._transport.app.state, "ingest_pipeline", pipeline)
    return pipeline


def _patch_http_post(client, monkeypatch, results=None, side_effect=None):
    """Patch ``app.state.http_client.post`` (narrowest scope) for semantic search."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"results": results or []}
    mock_post = AsyncMock(return_value=mock_resp, side_effect=side_effect)
    monkeypatch.setattr(client._transport.app.state.http_client, "post", mock_post)
    return mock_post


# ---------------------------------------------------------------------------
# POST /api/knowledge/ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_returns_pending(client, monkeypatch):
    pipeline = _pipeline(client, monkeypatch, item_id="item-123")
    resp = await client.post(
        "/api/knowledge/ingest",
        json={"url": "https://example.com/article", "title": "Test Article",
              "text": "Some pre-provided content.", "categories": ["Tech"],
              "source": "test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "item-123"
    assert data["status"] == "pending"
    kwargs = pipeline.submit_background.call_args.kwargs
    assert kwargs["url"] == "https://example.com/article"
    assert kwargs["title"] == "Test Article"
    assert kwargs["categories"] == ["Tech"]
    assert kwargs["source"] == "test"


@pytest.mark.asyncio
async def test_ingest_pipeline_error_returns_500(client, monkeypatch):
    pipeline = AsyncMock()
    pipeline.submit_background = AsyncMock(side_effect=RuntimeError("pipeline boom"))
    monkeypatch.setattr(client._transport.app.state, "ingest_pipeline", pipeline)
    resp = await client.post(
        "/api/knowledge/ingest",
        json={"url": "https://example.com/bad"},
    )
    assert resp.status_code == 500
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_ingest_missing_url_returns_422(client, monkeypatch):
    _pipeline(client, monkeypatch)
    resp = await client.post("/api/knowledge/ingest", json={"title": "No URL"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/knowledge/items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_items_empty(client, monkeypatch):
    _store(client, monkeypatch, list_items=[])
    resp = await client.get("/api/knowledge/items")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_list_items_returns_seeded_items(client, monkeypatch):
    _store(
        client,
        monkeypatch,
        list_items=[
            {"id": "item-1", "title": "First", "status": "ready"},
            {"id": "item-2", "title": "Second", "status": "pending"},
        ],
    )
    resp = await client.get("/api/knowledge/items")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["items"][0]["id"] == "item-1"
    assert data["items"][1]["id"] == "item-2"


@pytest.mark.asyncio
async def test_list_items_forwards_filters(client, monkeypatch):
    store = _store(client, monkeypatch, list_items=[])
    resp = await client.get(
        "/api/knowledge/items?source_type=reddit&status=ready&category=Tech&limit=5&offset=2"
    )
    assert resp.status_code == 200
    kwargs = store.list_items.call_args.kwargs
    assert kwargs["source_type"] == "reddit"
    assert kwargs["status"] == "ready"
    assert kwargs["category"] == "Tech"
    assert kwargs["limit"] == 5
    assert kwargs["offset"] == 2


# ---------------------------------------------------------------------------
# GET /api/knowledge/items/{item_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_item_returns_item(client, monkeypatch):
    _store(
        client,
        monkeypatch,
        get_item={"id": "item-1", "title": "Test", "status": "done",
                  "source_url": "https://example.com/1", "user_id": ""},
    )
    resp = await client.get("/api/knowledge/items/item-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "item-1"
    assert data["title"] == "Test"
    assert data["source_url"] == "https://example.com/1"


@pytest.mark.asyncio
async def test_get_item_not_found(client, monkeypatch):
    _store(client, monkeypatch, get_item=None)
    resp = await client.get("/api/knowledge/items/unknown-id-1234")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/knowledge/items/{item_id}/snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_snapshots_returns_snapshots(client, monkeypatch):
    _store(
        client,
        monkeypatch,
        get_item={"id": "item-1", "title": "T", "source_url": "u", "user_id": ""},
        list_snapshots=[{"id": 1, "item_id": "item-1", "content_hash": "abc",
                         "diff_json": {}, "metadata_json": {}, "snapshot_at": 1.0}],
    )
    resp = await client.get("/api/knowledge/items/item-1/snapshots")
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshots" in data
    assert len(data["snapshots"]) == 1
    assert data["snapshots"][0]["content_hash"] == "abc"


@pytest.mark.asyncio
async def test_list_snapshots_item_not_found(client, monkeypatch):
    _store(client, monkeypatch, get_item=None)
    resp = await client.get("/api/knowledge/items/item-1/snapshots")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/knowledge/items/{item_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_item_success(client, monkeypatch):
    _store(
        client,
        monkeypatch,
        get_item={"id": "item-1", "title": "T", "source_url": "u", "user_id": ""},
        delete_item=True,
    )
    resp = await client.delete("/api/knowledge/items/item-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deleted"
    assert data["id"] == "item-1"


@pytest.mark.asyncio
async def test_delete_item_not_found(client, monkeypatch):
    _store(client, monkeypatch, get_item=None)
    resp = await client.delete("/api/knowledge/items/unknown-id-5678")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_item_forbidden_for_non_admin(app, client, monkeypatch):
    _store(
        client,
        monkeypatch,
        get_item={"id": "item-1", "title": "T", "source_url": "u", "user_id": "owner"},
        delete_item=True,
    )
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "member",
        "username": "member",
        "full_name": "Member",
        "email": "",
        "is_admin": False,
        "pending": False,
        "created_at": 1,
        "capabilities": [],
        "last_login_at": 1,
    }
    try:
        resp = await client.delete("/api/knowledge/items/item-1")
        assert resp.status_code == 403
        assert resp.json().get("error") == "forbidden"
        # The store delete must never be reached when ownership fails.
        assert not client._transport.app.state.knowledge_store.delete_item.await_count
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# GET /api/knowledge/search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_keyword(client, monkeypatch):
    store = _store(
        client,
        monkeypatch,
        search_fts=[{"id": "r1", "title": "Asyncio Guide", "source_url": "u"}],
    )
    resp = await client.get("/api/knowledge/search?q=asyncio&mode=keyword&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "keyword"
    assert len(data["results"]) == 1
    assert data["results"][0]["id"] == "r1"
    assert store.search_fts.call_args.args[0] == "asyncio"


@pytest.mark.asyncio
async def test_search_semantic(client, monkeypatch):
    _store(client, monkeypatch)
    _patch_http_post(
        client,
        monkeypatch,
        results=[{"id": "r1", "score": 0.9}, {"id": "r2", "score": 0.8}],
    )
    resp = await client.get("/api/knowledge/search?q=vectors&mode=semantic")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "semantic"
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_search_semantic_falls_back_to_keyword(client, monkeypatch):
    _store(
        client,
        monkeypatch,
        search_fts=[{"id": "r1", "title": "Fallback", "source_url": "u"}],
    )
    _patch_http_post(client, monkeypatch, side_effect=ConnectionError("qmd down"))
    resp = await client.get("/api/knowledge/search?q=asyncio&mode=semantic")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "keyword"
    assert len(data["results"]) == 1


@pytest.mark.asyncio
async def test_search_empty_returns_keyword_mode(client, monkeypatch):
    _store(client, monkeypatch, search_fts=[])
    resp = await client.get("/api/knowledge/search?q=nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "keyword"
    assert data["results"] == []


# ---------------------------------------------------------------------------
# GET /api/knowledge/rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rules_empty(client, monkeypatch):
    _store(client, monkeypatch, list_rules=[])
    resp = await client.get("/api/knowledge/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data
    assert data["rules"] == []


@pytest.mark.asyncio
async def test_list_rules_returns_seeded_rules(client, monkeypatch):
    _store(
        client,
        monkeypatch,
        list_rules=[
            {"id": 1, "pattern": "test-*", "match_on": "title",
             "category": "tests", "priority": 10},
        ],
    )
    resp = await client.get("/api/knowledge/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["rules"]) == 1
    assert data["rules"][0]["pattern"] == "test-*"
    assert data["rules"][0]["category"] == "tests"


# ---------------------------------------------------------------------------
# POST /api/knowledge/rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule(client, monkeypatch):
    _store(client, monkeypatch, add_rule=1)
    resp = await client.post(
        "/api/knowledge/rules",
        json={"pattern": "python", "match_on": "title", "category": "Tech", "priority": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["status"] == "created"


@pytest.mark.asyncio
async def test_create_rule_missing_fields_returns_422(client, monkeypatch):
    _store(client, monkeypatch, add_rule=1)
    resp = await client.post("/api/knowledge/rules", json={"priority": 1})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/knowledge/rules/{rule_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_rule_success(client, monkeypatch):
    _store(client, monkeypatch, delete_rule=True)
    resp = await client.delete("/api/knowledge/rules/7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deleted"
    assert data["id"] == 7


@pytest.mark.asyncio
async def test_delete_rule_not_found(client, monkeypatch):
    _store(client, monkeypatch, delete_rule=False)
    resp = await client.delete("/api/knowledge/rules/999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/knowledge/subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_subscriptions_empty(client, monkeypatch):
    _store(client, monkeypatch, list_subscriptions=[])
    resp = await client.get("/api/knowledge/subscriptions")
    assert resp.status_code == 200
    data = resp.json()
    assert "subscriptions" in data
    assert data["subscriptions"] == []


@pytest.mark.asyncio
async def test_list_subscriptions_forwards_agent_filter(client, monkeypatch):
    store = _store(
        client,
        monkeypatch,
        list_subscriptions=[
            {"agent_name": "research-agent", "category": "AI/ML", "auto_ingest": True},
        ],
    )
    resp = await client.get("/api/knowledge/subscriptions?agent_name=research-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["subscriptions"]) == 1
    assert data["subscriptions"][0]["agent_name"] == "research-agent"
    assert store.list_subscriptions.call_args.kwargs["agent_name"] == "research-agent"


# ---------------------------------------------------------------------------
# POST /api/knowledge/subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_subscription(client, monkeypatch):
    store = _store(client, monkeypatch)
    resp = await client.post(
        "/api/knowledge/subscriptions",
        json={"agent_name": "test-agent", "category": "Tech", "auto_ingest": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    kwargs = store.set_subscription.call_args.kwargs
    assert kwargs["agent_name"] == "test-agent"
    assert kwargs["category"] == "Tech"
    assert kwargs["auto_ingest"] is False


@pytest.mark.asyncio
async def test_set_subscription_missing_fields_returns_422(client, monkeypatch):
    _store(client, monkeypatch)
    resp = await client.post("/api/knowledge/subscriptions", json={"auto_ingest": False})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/knowledge/subscriptions/{agent_name}/{category}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_subscription_success(client, monkeypatch):
    _store(client, monkeypatch, delete_subscription=True)
    resp = await client.delete("/api/knowledge/subscriptions/test-agent/Tech")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_subscription_not_found(client, monkeypatch):
    _store(client, monkeypatch, delete_subscription=False)
    resp = await client.delete("/api/knowledge/subscriptions/test-agent/Unknown")
    assert resp.status_code == 404
