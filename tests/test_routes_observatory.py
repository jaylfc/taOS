from httpx import ASGITransport, AsyncClient

import pytest

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.auth_context import CurrentUser, current_user


async def _non_admin_client(app):
    """Cookie'd client for a non-admin member session on *app*."""
    auth_mgr = app.state.auth
    try:
        invite_code = auth_mgr.add_user_invite("bob", "admin")
    except ValueError:
        invite_code = None
    if invite_code:
        auth_mgr.complete_invite("bob", invite_code, "Bob", "", "bobpass123")
    bob = auth_mgr.find_user("bob")
    token = auth_mgr.create_session(user_id=bob["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


async def _make_obs_agent_token(app, *, scopes=("observatory_control",), project_id=None):
    """Register an agent and add grants directly to the store (bypasses the
    route-layer scope-validation path), then return (canonical_id, signed JWT)."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    if registry._db is None:
        await registry.init()
    if grants._db is None:
        await grants.init()
    priv, _pub = app.state.agent_registry_keypair

    rec = await registry.register(
        framework="taosctl",
        display_name="Obs Agent",
        origin="taos-deployed",
        handle="@obs-agent",
    )
    cid = rec["canonical_id"]
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(cid, priv, user_id="u", framework="taosctl")
    return cid, token


def _bare(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


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
    resp = await client.get("/api/observatory/pause")
    assert resp.json()["global"] is True
    resp = await client.post("/api/observatory/pause", json={"scope": "global", "paused": False})
    assert resp.json()["global"] is False


@pytest.mark.asyncio
async def test_per_lane_pause_and_resume(client):
    lane = "@taOS-dev-kilo-owl-alpha"
    resp = await client.post("/api/observatory/pause", json={"scope": lane, "paused": True})
    assert resp.json()["lanes"].get(lane) is True
    assert resp.json()["global"] is False
    resp = await client.post("/api/observatory/pause", json={"scope": lane, "paused": False})
    assert lane not in resp.json()["lanes"]


@pytest.mark.asyncio
async def test_empty_scope_rejected(client):
    resp = await client.post("/api/observatory/pause", json={"scope": "  ", "paused": True})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_admin_cannot_pause(app, client):
    member_client = await _non_admin_client(app)
    try:
        resp = await member_client.post("/api/observatory/pause", json={"scope": "global", "paused": True})
        assert resp.status_code == 403
    finally:
        await member_client.aclose()


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
    resp = await client.post("/api/observatory/throttle", json={"scope": "global", "max_concurrent": 0})
    assert resp.json()["global"] is None


@pytest.mark.asyncio
async def test_per_lane_throttle_set_and_clear(client):
    lane = "@taOS-dev-kilo-owl-alpha"
    resp = await client.post("/api/observatory/throttle", json={"scope": lane, "max_concurrent": 3})
    assert resp.json()["lanes"].get(lane) == 3
    assert resp.json()["global"] is None
    resp = await client.post("/api/observatory/throttle", json={"scope": lane, "max_concurrent": None})
    assert lane not in resp.json()["lanes"]


@pytest.mark.asyncio
async def test_throttle_empty_scope_rejected(client):
    resp = await client.post("/api/observatory/throttle", json={"scope": "  ", "max_concurrent": 2})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_admin_cannot_throttle(app, client):
    member_client = await _non_admin_client(app)
    try:
        resp = await member_client.post("/api/observatory/throttle", json={"scope": "global", "max_concurrent": 1})
        assert resp.status_code == 403
    finally:
        await member_client.aclose()


@pytest.mark.asyncio
async def test_approval_mode_defaults_to_default(client):
    resp = await client.get("/api/observatory/approval-mode")
    assert resp.status_code == 200
    assert resp.json() == {"global": "default", "sessions": {}}


@pytest.mark.asyncio
async def test_global_approval_mode_round_trips(client):
    resp = await client.post("/api/observatory/approval-mode", json={"scope": "global", "mode": "accept_edits"})
    assert resp.status_code == 200
    assert resp.json()["global"] == "accept_edits"
    resp = await client.get("/api/observatory/approval-mode")
    assert resp.json()["global"] == "accept_edits"
    resp = await client.post("/api/observatory/approval-mode", json={"scope": "global", "mode": "default"})
    assert resp.json()["global"] == "default"


@pytest.mark.asyncio
async def test_per_session_approval_mode_set_and_clear(client):
    session = "cs-abc123"
    resp = await client.post("/api/observatory/approval-mode", json={"scope": session, "mode": "dont_ask"})
    assert resp.json()["sessions"] == {session: "dont_ask"}
    resp = await client.post("/api/observatory/approval-mode", json={"scope": session, "mode": "default"})
    assert resp.json()["sessions"] == {}


@pytest.mark.asyncio
async def test_approval_mode_tolerates_malformed_file(app, client):
    from pathlib import Path
    p = Path(app.state.data_dir) / "observatory_approval_mode.json"
    p.write_text('"not a dict"')
    r = await client.get("/api/observatory/approval-mode")
    assert r.status_code == 200
    assert r.json() == {"global": "default", "sessions": {}}
    p.write_text('{"global": "accept_edits", "sessions": "oops"}')
    r = await client.get("/api/observatory/approval-mode")
    assert r.status_code == 200
    assert r.json()["global"] == "accept_edits"
    assert r.json()["sessions"] == {}


@pytest.mark.asyncio
async def test_approval_mode_invalid_mode_rejected(client):
    resp = await client.post("/api/observatory/approval-mode", json={"scope": "global", "mode": "yolo"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_approval_mode_empty_scope_rejected(client):
    resp = await client.post("/api/observatory/approval-mode", json={"scope": "  ", "mode": "accept_edits"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_admin_cannot_set_approval_mode(app, client):
    member_client = await _non_admin_client(app)
    try:
        resp = await member_client.post("/api/observatory/approval-mode", json={"scope": "global", "mode": "dont_ask"})
        assert resp.status_code == 403
    finally:
        await member_client.aclose()


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


@pytest.mark.asyncio
async def test_fleet_idle_agent_carries_its_framework(app, client):
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    await reg.register(framework="hermes", display_name="Idle Fw",
                       handle="@lane-fw-idle", user_id="admin")
    agents = (await client.get("/api/observatory/fleet")).json()["agents"]
    mine = [a for a in agents if a["handle"] == "@lane-fw-idle"]
    assert len(mine) == 1
    assert mine[0]["framework"] == "hermes"


@pytest.mark.asyncio
async def test_fleet_working_agent_backfills_framework_from_registry(app, client):
    pstore = app.state.project_store
    tstore = app.state.project_task_store
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    await reg.register(framework="kilo", display_name="Busy Fw",
                       handle="@lane-fw-busy", user_id="admin")
    proj = await pstore.create_project(name="ObsFw", slug="obs-fw", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Card", created_by="admin")
    await tstore.claim_task(task["id"], "@lane-fw-busy")

    agents = (await client.get("/api/observatory/fleet")).json()["agents"]
    mine = [a for a in agents if a["handle"] == "@lane-fw-busy"]
    assert len(mine) == 1
    assert mine[0]["state"] == "working"
    assert mine[0]["framework"] == "kilo"


@pytest.mark.asyncio
async def test_fleet_unregistered_working_agent_has_empty_framework(app, client):
    pstore = app.state.project_store
    tstore = app.state.project_task_store
    proj = await pstore.create_project(name="ObsNoFw", slug="obs-no-fw", created_by="admin", user_id="admin")
    task = await tstore.create_task(proj["id"], title="Card", created_by="admin")
    await tstore.claim_task(task["id"], "@lane-unregistered")

    agents = (await client.get("/api/observatory/fleet")).json()["agents"]
    mine = [a for a in agents if a["handle"] == "@lane-unregistered"]
    assert len(mine) == 1
    assert mine[0]["framework"] == ""


class TestObservatoryAgentAuth:
    """Agent-token authentication for observatory routes.

    An agent reads/writes /api/observatory/* with its own Ed25519 registry JWT
    (scope observatory_control), never the owner session/password.  The admin
    check runs before any JWT verification so a local admin token is never
    mis-verified as a registry JWT.  Writes require a GLOBAL (null-project)
    grant; reads accept any active observatory_control grant.
    """

    @pytest.mark.asyncio
    async def test_agent_with_observatory_control_can_read_pause(self, app):
        _cid, token = await _make_obs_agent_token(app)
        async with _bare(app) as bare:
            resp = await bare.get(
                "/api/observatory/pause",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_agent_with_observatory_control_can_set_pause(self, app):
        _cid, token = await _make_obs_agent_token(app)
        async with _bare(app) as bare:
            resp = await bare.post(
                "/api/observatory/pause",
                json={"scope": "global", "paused": True},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["global"] is True

    @pytest.mark.asyncio
    async def test_agent_with_observatory_control_can_set_throttle(self, app):
        _cid, token = await _make_obs_agent_token(app)
        async with _bare(app) as bare:
            resp = await bare.post(
                "/api/observatory/throttle",
                json={"scope": "global", "max_concurrent": 3},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["global"] == 3

    @pytest.mark.asyncio
    async def test_agent_without_observatory_control_cannot_read(self, app):
        _cid, token = await _make_obs_agent_token(app, scopes=("memory_read",))
        async with _bare(app) as bare:
            resp = await bare.get(
                "/api/observatory/pause",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_agent_without_observatory_control_cannot_write(self, app):
        _cid, token = await _make_obs_agent_token(app, scopes=("memory_read",))
        async with _bare(app) as bare:
            resp = await bare.post(
                "/api/observatory/pause",
                json={"scope": "global", "paused": True},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_project_scoped_observatory_control_can_read_but_not_write(self, app):
        _cid, token = await _make_obs_agent_token(
            app, scopes=("observatory_control",), project_id="prj-obs"
        )
        async with _bare(app) as bare:
            read_resp = await bare.get(
                "/api/observatory/pause",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert read_resp.status_code == 200
        async with _bare(app) as bare:
            write_resp = await bare.post(
                "/api/observatory/pause",
                json={"scope": "global", "paused": True},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert write_resp.status_code == 403

    @pytest.mark.asyncio
    async def test_local_admin_token_can_read_and_write(self, app):
        if not app.state.auth.find_user("admin"):
            app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        local_token = app.state.auth.get_local_token()
        async with _bare(app) as bare:
            read_resp = await bare.get(
                "/api/observatory/pause",
                headers={"Authorization": f"Bearer {local_token}"},
            )
        assert read_resp.status_code == 200
        async with _bare(app) as bare:
            write_resp = await bare.post(
                "/api/observatory/pause",
                json={"scope": "global", "paused": True},
                headers={"Authorization": f"Bearer {local_token}"},
            )
        assert write_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_malformed_agent_token_gets_401(self, app):
        async with _bare(app) as bare:
            resp = await bare.get(
                "/api/observatory/pause",
                headers={"Authorization": "Bearer not-a-valid-token"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_gets_401(self, app):
        async with _bare(app) as bare:
            resp = await bare.get("/api/observatory/pause")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_global_grant_agent_sees_fleet(self, app):
        pstore = app.state.project_store
        tstore = app.state.project_task_store
        if pstore._db is None:
            await pstore.init()
        if tstore._db is None:
            await tstore.init()
        proj = await pstore.create_project(name="ObsG", slug="obs-global", created_by="admin", user_id="admin")
        task = await tstore.create_task(proj["id"], title="Global card", created_by="admin")
        await tstore.claim_task(task["id"], "@lane-global")

        _cid, token = await _make_obs_agent_token(app)
        async with _bare(app) as bare:
            resp = await bare.get(
                "/api/observatory/fleet",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        mine = [a for a in agents if a["handle"] == "@lane-global"]
        assert len(mine) == 1
        assert mine[0]["state"] == "working"

    @pytest.mark.asyncio
    async def test_project_scoped_agent_does_not_see_other_projects_lanes(self, app):
        pstore = app.state.project_store
        tstore = app.state.project_task_store
        if pstore._db is None:
            await pstore.init()
        if tstore._db is None:
            await tstore.init()
        proj_a = await pstore.create_project(name="ObsA", slug="obs-a", created_by="admin", user_id="admin")
        proj_b = await pstore.create_project(name="ObsB", slug="obs-b", created_by="admin", user_id="admin")
        task_a = await tstore.create_task(proj_a["id"], title="Card A", created_by="admin")
        task_b = await tstore.create_task(proj_b["id"], title="Card B", created_by="admin")
        await tstore.claim_task(task_a["id"], "@lane-a")
        await tstore.claim_task(task_b["id"], "@lane-b")

        _cid, token = await _make_obs_agent_token(
            app, scopes=("observatory_control",), project_id=proj_a["id"]
        )
        async with _bare(app) as bare:
            resp = await bare.get(
                "/api/observatory/fleet",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        handles = [a["handle"] for a in agents]
        assert "@lane-a" in handles
        assert "@lane-b" not in handles


@pytest.mark.asyncio
class TestObservatoryWakeBudget:
    async def test_fleet_wake_budget_admin(self, client):
        resp = await client.get("/api/observatory/wake-budget")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        for row in data["agents"]:
            assert "agent_id" in row
            assert "budget" in row
            assert "consumed" in row
            assert "remaining" in row
            assert "next_wake_epoch" in row

    async def test_fleet_wake_budget_agent_token(self, app):
        _cid, token = await _make_obs_agent_token(app, scopes=("observatory_control",))
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.get(
                "/api/observatory/wake-budget",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
