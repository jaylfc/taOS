from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path
from httpx import ASGITransport, AsyncClient
from tinyagentos.app import create_app
import yaml
from taos_test_csrf import csrf_event_hooks


@pytest_asyncio.fixture
async def knowledge_client(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    # Init required stores
    await app.state.metrics.init()
    await app.state.notifications.init()
    await app.state.qmd_client.init()
    await app.state.knowledge_store.init()

    # Auth middleware requires a configured user — set up a test admin so
    # all routes respond normally instead of returning 401.
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _record = app.state.auth.find_user("admin")
    _uid = _record["id"] if _record else ""
    _token = app.state.auth.create_session(user_id=_uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": _token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c

    await app.state.knowledge_store.close()
    await app.state.notifications.close()
    await app.state.metrics.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_ingest_returns_item_id(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/test",
        "title": "Test Article",
        "text": "Some pre-provided content that is long enough for testing purposes.",
        "categories": ["Tech"],
        "source": "test",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_list_items_empty(knowledge_client):
    resp = await knowledge_client.get("/api/knowledge/items")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_get_item(knowledge_client):
    # Ingest first
    ingest_resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/get-test",
        "title": "Get Test",
        "text": "Content for get test endpoint.",
        "categories": [],
        "source": "test",
    })
    item_id = ingest_resp.json()["id"]

    resp = await knowledge_client.get(f"/api/knowledge/items/{item_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == item_id
    assert data["source_url"] == "https://example.com/get-test"


@pytest.mark.asyncio
async def test_get_item_not_found(knowledge_client):
    resp = await knowledge_client.get("/api/knowledge/items/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_item(knowledge_client):
    ingest_resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/delete-test",
        "title": "Delete Test",
        "text": "Content for delete test.",
        "categories": [],
        "source": "test",
    })
    item_id = ingest_resp.json()["id"]

    del_resp = await knowledge_client.delete(f"/api/knowledge/items/{item_id}")
    assert del_resp.status_code == 200

    get_resp = await knowledge_client.get(f"/api/knowledge/items/{item_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_items_filter_by_source_type(knowledge_client):
    await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://reddit.com/r/test/comments/abc",
        "title": "Reddit post",
        "text": "Reddit content.",
        "categories": [],
        "source": "test",
    })
    await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/article",
        "title": "Article",
        "text": "Article content.",
        "categories": [],
        "source": "test",
    })
    resp = await knowledge_client.get("/api/knowledge/items?source_type=reddit")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["source_type"] == "reddit" for i in items)


@pytest.mark.asyncio
async def test_search_empty(knowledge_client):
    resp = await knowledge_client.get("/api/knowledge/search?q=nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["mode"] == "keyword"


@pytest.mark.asyncio
async def test_list_rules_empty(knowledge_client):
    resp = await knowledge_client.get("/api/knowledge/rules")
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data
    assert isinstance(data["rules"], list)


@pytest.mark.asyncio
async def test_create_and_delete_rule(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/rules", json={
        "pattern": "python",
        "match_on": "title",
        "category": "Tech",
        "priority": 1,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    rule_id = data["id"]

    del_resp = await knowledge_client.delete(f"/api/knowledge/rules/{rule_id}")
    assert del_resp.status_code == 200


@pytest.mark.asyncio
async def test_list_subscriptions_empty(knowledge_client):
    resp = await knowledge_client.get("/api/knowledge/subscriptions")
    assert resp.status_code == 200
    data = resp.json()
    assert "subscriptions" in data
    assert isinstance(data["subscriptions"], list)


@pytest.mark.asyncio
async def test_add_and_delete_subscription(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/subscriptions", json={
        "agent_name": "test-agent",
        "category": "Tech",
        "auto_ingest": False,
    })
    assert resp.status_code == 200

    list_resp = await knowledge_client.get("/api/knowledge/subscriptions?agent_name=test-agent")
    assert list_resp.status_code == 200
    subs = list_resp.json()["subscriptions"]
    assert len(subs) == 1

    del_resp = await knowledge_client.delete("/api/knowledge/subscriptions/test-agent/Tech")
    assert del_resp.status_code == 200

    list_resp2 = await knowledge_client.get("/api/knowledge/subscriptions?agent_name=test-agent")
    assert list_resp2.json()["subscriptions"] == []


@pytest.mark.asyncio
async def test_snapshots_empty(knowledge_client):
    ingest_resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/snap-test",
        "title": "Snap Test",
        "text": "Content.",
        "categories": [],
        "source": "test",
    })
    item_id = ingest_resp.json()["id"]

    resp = await knowledge_client.get(f"/api/knowledge/items/{item_id}/snapshots")
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshots" in data
    assert isinstance(data["snapshots"], list)


# ------------------------------------------------------------------
# Task 7: deeper route tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_keyword(knowledge_client):
    await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/asyncio-guide",
        "title": "Asyncio Guide",
        "text": "asyncio event loop coroutine await gather",
        "categories": ["Tech"],
        "source": "test",
    })
    resp = await knowledge_client.get("/api/knowledge/search?q=asyncio&mode=keyword&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["mode"] == "keyword"


@pytest.mark.asyncio
async def test_list_snapshots_empty(knowledge_client):
    ingest_resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/snap-test-2",
        "title": "Snapshot Test",
        "text": "Content.",
        "categories": [],
        "source": "test",
    })
    item_id = ingest_resp.json()["id"]
    resp = await knowledge_client.get(f"/api/knowledge/items/{item_id}/snapshots")
    assert resp.status_code == 200
    assert resp.json()["snapshots"] == []


@pytest.mark.asyncio
async def test_create_and_list_rules(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/rules", json={
        "pattern": "LocalLLaMA",
        "match_on": "subreddit",
        "category": "AI/ML",
        "priority": 10,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"

    list_resp = await knowledge_client.get("/api/knowledge/rules")
    assert list_resp.status_code == 200
    rules = list_resp.json()["rules"]
    assert any(r["category"] == "AI/ML" for r in rules)


@pytest.mark.asyncio
async def test_delete_rule(knowledge_client):
    create_resp = await knowledge_client.post("/api/knowledge/rules", json={
        "pattern": "temp*",
        "match_on": "source_type",
        "category": "Temp",
        "priority": 0,
    })
    rule_id = create_resp.json()["id"]
    del_resp = await knowledge_client.delete(f"/api/knowledge/rules/{rule_id}")
    assert del_resp.status_code == 200

    list_resp = await knowledge_client.get("/api/knowledge/rules")
    assert not any(r["id"] == rule_id for r in list_resp.json()["rules"])


@pytest.mark.asyncio
async def test_create_and_list_subscriptions(knowledge_client):
    resp = await knowledge_client.post("/api/knowledge/subscriptions", json={
        "agent_name": "research-agent",
        "category": "AI/ML",
        "auto_ingest": True,
    })
    assert resp.status_code == 200

    list_resp = await knowledge_client.get("/api/knowledge/subscriptions?agent_name=research-agent")
    assert list_resp.status_code == 200
    subs = list_resp.json()["subscriptions"]
    assert len(subs) == 1
    assert subs[0]["category"] == "AI/ML"
    assert subs[0]["auto_ingest"] is True


@pytest.mark.asyncio
async def test_delete_subscription(knowledge_client):
    await knowledge_client.post("/api/knowledge/subscriptions", json={
        "agent_name": "dev-agent",
        "category": "Development",
        "auto_ingest": False,
    })
    del_resp = await knowledge_client.delete(
        "/api/knowledge/subscriptions/dev-agent/Development"
    )
    assert del_resp.status_code == 200

    list_resp = await knowledge_client.get("/api/knowledge/subscriptions?agent_name=dev-agent")
    assert list_resp.json()["subscriptions"] == []


# ------------------------------------------------------------------
# Task 8: lifespan smoke tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_knowledge_store_in_app_state(knowledge_client):
    """Verify knowledge_store is accessible on app state after startup."""
    # The client fixture initialises the store — just verify the list endpoint works
    resp = await knowledge_client.get("/api/knowledge/items")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ingest_pipeline_in_app_state(knowledge_client):
    """Verify ingest_pipeline is accessible and returns a valid item id."""
    resp = await knowledge_client.post("/api/knowledge/ingest", json={
        "url": "https://example.com/smoke-test",
        "title": "Smoke Test",
        "text": "Smoke test content.",
        "categories": [],
        "source": "smoke",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["id"]) == 36  # UUID length
