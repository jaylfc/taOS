import pytest

from tinyagentos.notifications import NotificationStore


@pytest.mark.asyncio
async def test_get_prefs_returns_all_event_types_default_unmuted(client):
    r = await client.get("/api/notifications/prefs")
    assert r.status_code == 200
    prefs = r.json()
    assert len(prefs) == len(NotificationStore.EVENT_TYPES)
    assert {p["event_type"] for p in prefs} == set(NotificationStore.EVENT_TYPES)
    for pref in prefs:
        assert pref["muted"] is False


@pytest.mark.asyncio
async def test_put_valid_event_sets_muted_and_get_reflects(client):
    event_type = next(iter(NotificationStore.EVENT_TYPES))
    r = await client.put(
        f"/api/notifications/prefs/{event_type}",
        json={"muted": True},
    )
    assert r.status_code == 200
    assert r.json() == {"event_type": event_type, "muted": True}

    r = await client.get("/api/notifications/prefs")
    assert r.status_code == 200
    worker = next(p for p in r.json() if p["event_type"] == event_type)
    assert worker["muted"] is True


@pytest.mark.asyncio
async def test_put_unknown_event_type_returns_404(client):
    r = await client.put(
        "/api/notifications/prefs/not.a.real.event",
        json={"muted": True},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "unknown_event_type"


@pytest.mark.asyncio
async def test_put_invalid_or_missing_body_returns_400(client):
    r = await client.put("/api/notifications/prefs/worker.join")
    assert r.status_code == 400

    r = await client.put(
        "/api/notifications/prefs/worker.join",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400

    r = await client.put("/api/notifications/prefs/worker.join", json={})
    assert r.status_code == 400

    r = await client.put(
        "/api/notifications/prefs/worker.join",
        json={"muted": "yes"},
    )
    assert r.status_code == 400