"""Endpoint tests for tinyagentos/routes/browsing_history.py."""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def browsing_history(app, tmp_data_dir):
    """Initialise browsing_history on app.state so routes can access it."""
    from taosmd import BrowsingHistory as BrowsingHistoryStore

    store = BrowsingHistoryStore(db_path=tmp_data_dir / "browsing-history.db")
    await store.init()
    app.state.browsing_history = store
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_record_returns_200(client, browsing_history):
    resp = await client.post(
        "/api/browsing-history/record",
        json={
            "url": "https://example.com/article/1",
            "source_type": "web",
            "title": "Test Article",
            "author": "Author",
            "preview": "A preview",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_record_response_shape(client, browsing_history):
    resp = await client.post(
        "/api/browsing-history/record",
        json={
            "url": "https://example.com/article/2",
            "source_type": "web",
            "title": "",
        },
    )
    data = resp.json()
    assert data == {"status": "ok"}


@pytest.mark.asyncio
async def test_record_missing_url_returns_422(client, browsing_history):
    resp = await client.post(
        "/api/browsing-history/record",
        json={"source_type": "web"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_record_missing_source_type_returns_422(client, browsing_history):
    resp = await client.post(
        "/api/browsing-history/record",
        json={"url": "https://example.com/article/3"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_history_returns_200(client, browsing_history):
    resp = await client.get("/api/browsing-history")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_history_response_shape(client, browsing_history):
    await browsing_history.record(
        url="https://example.com/list-test",
        source_type="web",
        title="List Test",
    )
    resp = await client.get("/api/browsing-history")
    data = resp.json()
    assert "items" in data
    assert "count" in data
    assert isinstance(data["items"], list)
    assert isinstance(data["count"], int)
    assert data["count"] == len(data["items"])


@pytest.mark.asyncio
async def test_list_history_empty(client, browsing_history):
    resp = await client.get("/api/browsing-history")
    data = resp.json()
    assert data["items"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_list_history_with_source_type(client, browsing_history):
    await browsing_history.record(
        url="https://reddit.com/r/test",
        source_type="reddit",
        title="Reddit Post",
    )
    await browsing_history.record(
        url="https://example.com/web",
        source_type="web",
        title="Web Page",
    )
    resp = await client.get("/api/browsing-history?source_type=reddit")
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["source_type"] == "reddit"


@pytest.mark.asyncio
async def test_list_history_limit(client, browsing_history):
    for i in range(5):
        await browsing_history.record(
            url=f"https://example.com/limit-{i}",
            source_type="web",
            title=f"Item {i}",
        )
    resp = await client.get("/api/browsing-history?limit=3")
    data = resp.json()
    assert data["count"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_clear_history_returns_200(client, browsing_history):
    resp = await client.delete("/api/browsing-history")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_clear_history_response_shape(client, browsing_history):
    await browsing_history.record(
        url="https://example.com/clear-test",
        source_type="web",
        title="Clear Test",
    )
    resp = await client.delete("/api/browsing-history")
    data = resp.json()
    assert "deleted" in data
    assert isinstance(data["deleted"], int)
    assert data["deleted"] >= 1


@pytest.mark.asyncio
async def test_clear_history_empty(client, browsing_history):
    resp = await client.delete("/api/browsing-history")
    data = resp.json()
    assert data["deleted"] == 0


@pytest.mark.asyncio
async def test_clear_history_with_source_type(client, browsing_history):
    await browsing_history.record(
        url="https://reddit.com/r/clear",
        source_type="reddit",
        title="Reddit",
    )
    await browsing_history.record(
        url="https://example.com/clear-web",
        source_type="web",
        title="Web",
    )
    resp = await client.delete("/api/browsing-history?source_type=reddit")
    data = resp.json()
    assert data["deleted"] == 1
    remaining = await browsing_history.list_recent()
    assert len(remaining) == 1
    assert remaining[0]["source_type"] == "web"


@pytest.mark.asyncio
async def test_record_then_list_round_trip(client, browsing_history):
    await browsing_history.record(
        url="https://example.com/round-trip",
        source_type="web",
        title="Round Trip",
        author="Tester",
        preview="Preview text",
    )
    resp = await client.get("/api/browsing-history")
    data = resp.json()
    assert data["count"] == 1
    item = data["items"][0]
    assert item["url"] == "https://example.com/round-trip"
    assert item["source_type"] == "web"
    assert item["title"] == "Round Trip"
    assert item["author"] == "Tester"
    assert item["preview"] == "Preview text"
