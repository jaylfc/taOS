import pytest

from tinyagentos.auth_context import CurrentUser, current_user


@pytest.mark.asyncio
async def test_pause_defaults_to_unpaused(client):
    resp = await client.get("/api/observatory/pause")
    assert resp.status_code == 200
    assert resp.json() == {"global": False, "lanes": {}}


@pytest.mark.asyncio
async def test_global_pause_round_trips(client):
    resp = await client.post("/api/observatory/pause", json={"scope": "global", "paused": True})
    assert resp.status_code == 200
    assert resp.json()["global"] is True
    # A fresh read reflects it (persisted).
    resp = await client.get("/api/observatory/pause")
    assert resp.json()["global"] is True
    # Resume.
    resp = await client.post("/api/observatory/pause", json={"scope": "global", "paused": False})
    assert resp.json()["global"] is False


@pytest.mark.asyncio
async def test_per_lane_pause_and_resume(client):
    lane = "@taOS-dev-kilo-owl-alpha"
    resp = await client.post("/api/observatory/pause", json={"scope": lane, "paused": True})
    assert resp.json()["lanes"].get(lane) is True
    assert resp.json()["global"] is False  # lane pause does not touch global
    # Unpausing removes the lane entry rather than leaving a False.
    resp = await client.post("/api/observatory/pause", json={"scope": lane, "paused": False})
    assert lane not in resp.json()["lanes"]


@pytest.mark.asyncio
async def test_empty_scope_rejected(client):
    resp = await client.post("/api/observatory/pause", json={"scope": "  ", "paused": True})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_admin_cannot_pause(app, client):
    # Override the auth dependency to a non-admin user for this request.
    app.dependency_overrides[current_user] = lambda: CurrentUser(user_id="bob", is_admin=False)
    try:
        resp = await client.post("/api/observatory/pause", json={"scope": "global", "paused": True})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
