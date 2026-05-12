import pytest


@pytest.mark.asyncio
async def test_api_index_returns_routes(client):
    resp = await client.get("/api")
    assert resp.status_code == 200
    body = resp.json()
    assert "routes" in body
    routes = body["routes"]
    assert isinstance(routes, list)
    assert len(routes) > 0
    for r in routes:
        assert "prefix" in r
        assert "title" in r
        assert "doc_url" in r
    prefixes = [r["prefix"] for r in routes]
    assert "/api/agents" in prefixes


@pytest.mark.asyncio
async def test_api_index_includes_ui_notify_placeholder(client):
    """ui.notify is planned in Pass 1 Task 15; the discovery index lists it."""
    resp = await client.get("/api")
    assert resp.status_code == 200
    body = resp.json()
    prefixes = [r["prefix"] for r in body["routes"]]
    assert "/api/ui/notify" in prefixes
