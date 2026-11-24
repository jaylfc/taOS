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
    # The PUT now returns the full pref (#206 added style/show_centre).
    body = r.json()
    assert body["event_type"] == event_type
    assert body["muted"] is True
    assert body["style"] == "banner"
    assert body["show_centre"] is True

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

@pytest.mark.asyncio
async def test_get_prefs_include_style_and_centre_defaults(client):
    r = await client.get("/api/notifications/prefs")
    assert r.status_code == 200
    for pref in r.json():
        assert pref["style"] == "banner"
        assert pref["show_centre"] is True


@pytest.mark.asyncio
async def test_put_style_round_trip(client):
    r = await client.put(
        "/api/notifications/prefs/worker.join",
        json={"style": "alert"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["event_type"] == "worker.join"
    assert body["style"] == "alert"
    assert body["muted"] is False
    assert body["show_centre"] is True

    r = await client.get("/api/notifications/prefs")
    pref = next(p for p in r.json() if p["event_type"] == "worker.join")
    assert pref["style"] == "alert"


@pytest.mark.asyncio
async def test_put_show_centre_round_trip_and_mute_preserved(client):
    r = await client.put(
        "/api/notifications/prefs/backend.down",
        json={"show_centre": False},
    )
    assert r.status_code == 200
    assert r.json()["show_centre"] is False

    # A later muted-only PUT must not reset style/show_centre.
    r = await client.put(
        "/api/notifications/prefs/backend.down",
        json={"muted": True},
    )
    assert r.status_code == 200
    assert r.json()["muted"] is True
    assert r.json()["show_centre"] is False


@pytest.mark.asyncio
async def test_put_invalid_style_returns_400(client):
    r = await client.put(
        "/api/notifications/prefs/worker.join",
        json={"style": "loud"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_put_non_bool_show_centre_returns_400(client):
    r = await client.put(
        "/api/notifications/prefs/worker.join",
        json={"show_centre": "yes"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_dnd_get_defaults_and_put_round_trip(client):
    r = await client.get("/api/notifications/dnd")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "start": "22:00", "end": "07:00"}

    r = await client.put(
        "/api/notifications/dnd",
        json={"enabled": True, "start": "21:30", "end": "06:45"},
    )
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "start": "21:30", "end": "06:45"}

    r = await client.get("/api/notifications/dnd")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "start": "21:30", "end": "06:45"}


@pytest.mark.asyncio
async def test_dnd_put_invalid_time_returns_400(client):
    r = await client.put(
        "/api/notifications/dnd",
        json={"enabled": True, "start": "25:00", "end": "07:00"},
    )
    assert r.status_code == 400

    r = await client.put(
        "/api/notifications/dnd",
        json={"enabled": "yes", "start": "22:00", "end": "07:00"},
    )
    assert r.status_code == 400
