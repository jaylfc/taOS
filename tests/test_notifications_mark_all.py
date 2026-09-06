import pytest


@pytest.mark.asyncio
async def test_mark_all_read_returns_count(client):
    store = client._transport.app.state.notifications
    await store.add("A", "a")
    await store.add("B", "b")
    await store.add("C", "c")
    assert await store.unread_count() == 3
    resp = await client.post("/api/notifications/mark-all-read")
    assert resp.status_code == 200
    data = resp.json()
    assert data["marked"] == 3
    assert await store.unread_count() == 0


@pytest.mark.asyncio
async def test_mark_all_read_idempotent(client):
    store = client._transport.app.state.notifications
    await store.add("A", "a")
    resp = await client.post("/api/notifications/mark-all-read")
    assert resp.status_code == 200
    assert resp.json()["marked"] == 1
    resp = await client.post("/api/notifications/mark-all-read")
    assert resp.status_code == 200
    assert resp.json()["marked"] == 0


@pytest.mark.asyncio
async def test_mark_all_read_no_notifications(client):
    resp = await client.post("/api/notifications/mark-all-read")
    assert resp.status_code == 200
    assert resp.json()["marked"] == 0
