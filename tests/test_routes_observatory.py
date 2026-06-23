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


@pytest.mark.asyncio
async def test_fleet_empty_when_no_claims(client):
    resp = await client.get("/api/observatory/fleet")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents"] == []
    assert data["paused"] == {"global": False, "lanes": {}}


@pytest.mark.asyncio
async def test_fleet_shows_working_agent_with_held_card(app, client):
    pstore = app.state.project_store
    tstore = app.state.project_task_store
    proj = await pstore.create_project(name="Obs", slug="obs-fleet", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Build the thing", created_by="admin")
    claimed = await tstore.claim_task(task["id"], "@taOS-dev-kilo-owl-alpha")
    assert claimed is True

    resp = await client.get("/api/observatory/fleet")
    assert resp.status_code == 200
    agents = resp.json()["agents"]
    mine = [a for a in agents if a["handle"] == "@taOS-dev-kilo-owl-alpha"]
    assert len(mine) == 1
    assert mine[0]["state"] == "working"
    assert mine[0]["holds"]["task_id"] == task["id"]
    assert mine[0]["holds"]["title"] == "Build the thing"


@pytest.mark.asyncio
async def test_fleet_includes_idle_registered_agents(app, client):
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    await reg.register(framework="opencode", display_name="Side Agent",
                       handle="@side-agent", user_id="admin")
    resp = await client.get("/api/observatory/fleet")
    assert resp.status_code == 200
    agents = resp.json()["agents"]
    idle = [a for a in agents if a["handle"] == "@side-agent"]
    assert len(idle) == 1
    assert idle[0]["state"] == "idle"
    assert idle[0]["holds"] is None


@pytest.mark.asyncio
async def test_registered_agent_with_a_claim_is_working_not_idle(app, client):
    pstore = app.state.project_store
    tstore = app.state.project_task_store
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    await reg.register(framework="opencode", display_name="Busy Agent",
                       handle="@busy-agent", user_id="admin")
    proj = await pstore.create_project(name="Obs2", slug="obs-idle", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Do work", created_by="admin")
    await tstore.claim_task(task["id"], "@busy-agent")

    resp = await client.get("/api/observatory/fleet")
    rows = [a for a in resp.json()["agents"] if a["handle"] == "@busy-agent"]
    # The agent appears once, as working (not duplicated as idle).
    assert len(rows) == 1
    assert rows[0]["state"] == "working"
