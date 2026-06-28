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
async def test_throttle_defaults_to_no_caps(client):
    resp = await client.get("/api/observatory/throttle")
    assert resp.status_code == 200
    assert resp.json() == {"global": None, "lanes": {}}


@pytest.mark.asyncio
async def test_global_throttle_round_trips(client):
    resp = await client.post("/api/observatory/throttle", json={"scope": "global", "max_concurrent": 2})
    assert resp.status_code == 200
    assert resp.json()["global"] == 2
    resp = await client.get("/api/observatory/throttle")
    assert resp.json()["global"] == 2
    # max_concurrent <= 0 (or null) clears the cap.
    resp = await client.post("/api/observatory/throttle", json={"scope": "global", "max_concurrent": 0})
    assert resp.json()["global"] is None


@pytest.mark.asyncio
async def test_per_lane_throttle_set_and_clear(client):
    lane = "@taOS-dev-kilo-owl-alpha"
    resp = await client.post("/api/observatory/throttle", json={"scope": lane, "max_concurrent": 3})
    assert resp.json()["lanes"].get(lane) == 3
    assert resp.json()["global"] is None
    # Clearing (null) drops the lane entry rather than storing 0.
    resp = await client.post("/api/observatory/throttle", json={"scope": lane, "max_concurrent": None})
    assert lane not in resp.json()["lanes"]


@pytest.mark.asyncio
async def test_throttle_empty_scope_rejected(client):
    resp = await client.post("/api/observatory/throttle", json={"scope": "  ", "max_concurrent": 2})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_admin_cannot_throttle(app, client):
    app.dependency_overrides[current_user] = lambda: CurrentUser(user_id="bob", is_admin=False)
    try:
        resp = await client.post("/api/observatory/throttle", json={"scope": "global", "max_concurrent": 1})
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


@pytest.mark.asyncio
async def test_fleet_fresh_claim_has_held_seconds_and_is_not_stale(app, client):
    pstore = app.state.project_store
    tstore = app.state.project_task_store
    proj = await pstore.create_project(name="ObsFresh", slug="obs-fresh", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Fresh card", created_by="admin")
    await tstore.claim_task(task["id"], "@lane-fresh")

    agents = (await client.get("/api/observatory/fleet")).json()["agents"]
    mine = [a for a in agents if a["handle"] == "@lane-fresh"]
    assert len(mine) == 1
    assert mine[0]["held_seconds"] is not None and mine[0]["held_seconds"] >= 0
    assert mine[0]["stale"] is False


@pytest.mark.asyncio
async def test_fleet_flags_a_long_held_claim_as_stale(app, client):
    from tinyagentos.routes.observatory import STALE_CLAIM_SECONDS
    import time

    pstore = app.state.project_store
    tstore = app.state.project_task_store
    proj = await pstore.create_project(name="ObsStale", slug="obs-stale", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Wedged card", created_by="admin")
    await tstore.claim_task(task["id"], "@lane-stale")
    # Backdate the claim well past the threshold to exercise the stale path.
    old = time.time() - (STALE_CLAIM_SECONDS + 600)
    await tstore._db.execute(
        "UPDATE project_tasks SET claimed_at = ? WHERE id = ?", (old, task["id"]))
    await tstore._db.commit()

    agents = (await client.get("/api/observatory/fleet")).json()["agents"]
    mine = [a for a in agents if a["handle"] == "@lane-stale"]
    assert len(mine) == 1
    assert mine[0]["stale"] is True
    assert mine[0]["held_seconds"] >= STALE_CLAIM_SECONDS


@pytest.mark.asyncio
async def test_fleet_idle_agent_has_uniform_badge_shape(app, client):
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    await reg.register(framework="opencode", display_name="Idle One",
                       handle="@lane-idle", user_id="admin")
    agents = (await client.get("/api/observatory/fleet")).json()["agents"]
    idle = [a for a in agents if a["handle"] == "@lane-idle"]
    assert len(idle) == 1
    assert idle[0]["held_seconds"] is None
    assert idle[0]["stale"] is False


@pytest.mark.asyncio
async def test_fleet_clamps_held_seconds_under_clock_skew(app, client):
    import time

    pstore = app.state.project_store
    tstore = app.state.project_task_store
    proj = await pstore.create_project(name="ObsSkew", slug="obs-skew", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Future card", created_by="admin")
    await tstore.claim_task(task["id"], "@lane-skew")
    # Claim stamped slightly in the future (clock skew) must not yield a negative age.
    await tstore._db.execute(
        "UPDATE project_tasks SET claimed_at = ? WHERE id = ?", (time.time() + 600, task["id"]))
    await tstore._db.commit()

    agents = (await client.get("/api/observatory/fleet")).json()["agents"]
    mine = [a for a in agents if a["handle"] == "@lane-skew"]
    assert len(mine) == 1
    assert mine[0]["held_seconds"] == 0
    assert mine[0]["stale"] is False


@pytest.mark.asyncio
async def test_fleet_health_empty_is_idle(client):
    health = (await client.get("/api/observatory/fleet")).json()["health"]
    assert health == {
        "total": 0, "working": 0, "idle": 0, "stale": 0,
        "stale_handles": [], "status": "idle",
    }


@pytest.mark.asyncio
async def test_fleet_health_counts_working_and_idle(app, client):
    pstore = app.state.project_store
    tstore = app.state.project_task_store
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    proj = await pstore.create_project(name="ObsH", slug="obs-health", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="A card", created_by="admin")
    await tstore.claim_task(task["id"], "@lane-busy")
    await reg.register(framework="opencode", display_name="Idle H",
                       handle="@lane-free", user_id="admin")

    health = (await client.get("/api/observatory/fleet")).json()["health"]
    assert health["total"] == 2
    assert health["working"] == 1
    assert health["idle"] == 1
    assert health["stale"] == 0
    assert health["status"] == "active"


@pytest.mark.asyncio
async def test_fleet_health_degraded_when_stale(app, client):
    from tinyagentos.routes.observatory import STALE_CLAIM_SECONDS
    import time

    pstore = app.state.project_store
    tstore = app.state.project_task_store
    proj = await pstore.create_project(name="ObsHD", slug="obs-health-degraded", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Wedged", created_by="admin")
    await tstore.claim_task(task["id"], "@lane-wedged")
    old = time.time() - (STALE_CLAIM_SECONDS + 600)
    await tstore._db.execute(
        "UPDATE project_tasks SET claimed_at = ? WHERE id = ?", (old, task["id"]))
    await tstore._db.commit()

    health = (await client.get("/api/observatory/fleet")).json()["health"]
    assert health["stale"] == 1
    assert health["stale_handles"] == ["@lane-wedged"]
    assert health["status"] == "degraded"
