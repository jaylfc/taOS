"""Tests for Community View endpoints (Milestone F)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _add_member_user(app, username: str = "member", password: str = "memberpass1") -> str:
    """Inject a non-admin user into the auth store and return their user_id."""
    auth = app.state.auth
    invite_code = auth.add_user_invite(username, invited_by_username="admin")
    auth.complete_invite(
        username=username,
        invite_code=invite_code,
        full_name="Member User",
        email=f"{username}@test.local",
        password=password,
    )
    record = auth.find_user(username)
    return record["id"]


async def _init_project_stores(app):
    for attr in ("project_store", "project_task_store", "board_audit", "channels"):
        store = getattr(app.state, attr, None)
        if store is not None and store._db is None:
            await store.init()
    app.state.projects_root.mkdir(parents=True, exist_ok=True)


@pytest_asyncio.fixture
async def two_member_clients(app, tmp_data_dir):
    """Two separate non-admin member clients (alice and bob) sharing one app."""
    await _init_project_stores(app)
    alice_uid = _add_member_user(app, username="alice", password="alicepass1")
    bob_uid = _add_member_user(app, username="bob", password="bobspass1")
    alice_token = app.state.auth.create_session(user_id=alice_uid, long_lived=True)
    bob_token = app.state.auth.create_session(user_id=bob_uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": alice_token},
        event_hooks=csrf_event_hooks(),
    ) as alice_c:
        async with AsyncClient(
            transport=transport, base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as bob_c:
            alice_c._test_uid = alice_uid
            alice_c._test_app = app
            bob_c._test_uid = bob_uid
            bob_c._test_app = app
            yield alice_c, bob_c

    await app.state.project_store.close()
    await app.state.project_task_store.close()


# ---------------------------------------------------------------------------
# community/snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_community_snapshot_empty_project(client):
    """Snapshot returns metadata + empty lists for a project with no tasks."""
    resp = await client.post(
        "/api/projects",
        json={"name": "Test Project", "slug": "test-community", "description": "a test"},
    )
    assert resp.status_code == 200
    pid = resp.json()["id"]

    resp = await client.get(f"/api/projects/{pid}/community/snapshot")
    assert resp.status_code == 200
    body = resp.json()

    assert body["project"]["id"] == pid
    assert body["project"]["name"] == "Test Project"
    assert body["project"]["slug"] == "test-community"
    assert body["project"]["description"] == "a test"
    assert body["project"]["status"] == "active"
    assert isinstance(body["tasks"], list)
    assert len(body["tasks"]) == 0
    assert isinstance(body["status_counts"], dict)
    assert isinstance(body["contributors"], list)
    assert len(body["contributors"]) == 0
    assert isinstance(body["recent_activity"], list)
    assert len(body["recent_activity"]) == 0


@pytest.mark.asyncio
async def test_community_snapshot_with_tasks(client):
    """Snapshot includes allowlisted task fields and status breakdown."""
    resp = await client.post(
        "/api/projects",
        json={"name": "P", "slug": "p-with-tasks"},
    )
    pid = resp.json()["id"]

    # Create tasks in different statuses.
    for i in range(3):
        await client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": f"Task {i}", "priority": i},
        )
    # Claim one task.
    tasks = (await client.get(f"/api/projects/{pid}/tasks")).json()["items"]
    await client.post(
        f"/api/projects/{pid}/tasks/{tasks[0]['id']}/claim",
        json={"claimer_id": "agent-1"},
    )
    # Close another.
    await client.post(
        f"/api/projects/{pid}/tasks/{tasks[1]['id']}/close",
        json={"closed_by": "agent-1"},
    )

    resp = await client.get(f"/api/projects/{pid}/community/snapshot")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["tasks"]) == 3

    # Status breakdown.
    sc = body["status_counts"]
    assert sc.get("open", 0) == 1
    assert sc.get("claimed", 0) == 1
    assert sc.get("closed", 0) == 1

    # Sanitised task fields — only allowlisted keys.
    for t in body["tasks"]:
        assert "body" not in t  # body is NOT allowlisted
        assert "claimed_by" in t
        assert "id" in t
        assert "title" in t
        assert "status" in t

    # Leaderboard — contributors should have claim + close events.
    assert len(body["contributors"]) > 0
    contributor = body["contributors"][0]
    assert "actor" in contributor
    assert "claims" in contributor
    assert "closes" in contributor
    assert "total" in contributor


@pytest.mark.asyncio
async def test_community_snapshot_not_found(client):
    """404 for a nonexistent project."""
    resp = await client.get("/api/projects/nonexistent/community/snapshot")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_community_snapshot_cross_user_404(two_member_clients):
    """Non-owner gets masked 404, not 403, on another user's community snapshot."""
    alice, bob = two_member_clients
    # Alice creates a project.
    resp = await alice.post(
        "/api/projects",
        json={"name": "Alice Project", "slug": "alice-comm"},
    )
    pid = resp.json()["id"]

    # Alice can access her own snapshot.
    resp = await alice.get(f"/api/projects/{pid}/community/snapshot")
    assert resp.status_code == 200

    # Bob gets a masked 404 (project existence is not leaked).
    resp = await bob.get(f"/api/projects/{pid}/community/snapshot")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# community/stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_community_stats(client):
    """Stats endpoint returns status counts + leaderboard without full task list."""
    resp = await client.post(
        "/api/projects",
        json={"name": "Stats Project", "slug": "stats-proj"},
    )
    pid = resp.json()["id"]

    # Create + claim + close tasks to generate audit events.
    for i in range(2):
        await client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": f"S{i}"},
        )
    tasks = (await client.get(f"/api/projects/{pid}/tasks")).json()["items"]
    await client.post(
        f"/api/projects/{pid}/tasks/{tasks[0]['id']}/claim",
        json={"claimer_id": "claimer-a"},
    )
    await client.post(
        f"/api/projects/{pid}/tasks/{tasks[0]['id']}/close",
        json={"closed_by": "claimer-a"},
    )

    resp = await client.get(f"/api/projects/{pid}/community/stats")
    assert resp.status_code == 200
    body = resp.json()

    assert body["project_id"] == pid
    assert body["project_name"] == "Stats Project"
    assert isinstance(body["status_counts"], dict)
    assert isinstance(body["leaderboard"], list)

    # Stats should NOT include the full task list.
    assert "tasks" not in body

    # Leaderboard should have at least one contributor.
    assert len(body["leaderboard"]) > 0
    lb = body["leaderboard"][0]
    assert "actor" in lb
    assert "claims" in lb
    assert "closes" in lb
    assert "total" in lb


@pytest.mark.asyncio
async def test_community_stats_not_found(client):
    """404 for a nonexistent project."""
    resp = await client.get("/api/projects/nonexistent/community/stats")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_community_stats_cross_user_404(two_member_clients):
    """Non-owner gets masked 404 on another user's community stats."""
    alice, bob = two_member_clients
    resp = await alice.post(
        "/api/projects",
        json={"name": "Alice Stats", "slug": "alice-stats"},
    )
    pid = resp.json()["id"]

    # Alice can access her own stats.
    resp = await alice.get(f"/api/projects/{pid}/community/stats")
    assert resp.status_code == 200

    # Bob gets masked 404.
    resp = await bob.get(f"/api/projects/{pid}/community/stats")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Leaderboard rollup unit test (no HTTP — pure function)
# ---------------------------------------------------------------------------


def test_build_leaderboard():
    """Unit test for the leaderboard aggregation function."""
    from tinyagentos.routes.community import _build_leaderboard

    events = [
        {"actor": "agent-1", "event": "task.claimed", "task_id": "t1"},
        {"actor": "agent-1", "event": "task.closed", "task_id": "t1"},
        {"actor": "agent-2", "event": "task.claimed", "task_id": "t2"},
        {"actor": "agent-1", "event": "task.claimed", "task_id": "t3"},
        {"actor": "agent-3", "event": "claimed", "task_id": "t4"},
    ]

    lb = _build_leaderboard(events)

    # Sorted by total descending.
    assert len(lb) == 3
    assert lb[0]["actor"] == "agent-1"
    assert lb[0]["claims"] == 2
    assert lb[0]["closes"] == 1
    assert lb[0]["total"] == 3

    # agent-2 and agent-3 are tied at total=1 — order within ties is
    # insertion-order stable, so both orderings are valid.
    tied = {lb[1]["actor"], lb[2]["actor"]}
    assert tied == {"agent-2", "agent-3"}
    for entry in (lb[1], lb[2]):
        assert entry["claims"] == 1
        assert entry["closes"] == 0
        assert entry["total"] == 1


def test_build_leaderboard_empty():
    """Empty events give empty leaderboard."""
    from tinyagentos.routes.community import _build_leaderboard

    assert _build_leaderboard([]) == []


def test_build_leaderboard_ignores_unknown_events():
    """Events other than claim/close are ignored."""
    from tinyagentos.routes.community import _build_leaderboard

    events = [
        {"actor": "agent-1", "event": "task.created", "task_id": "t1"},
        {"actor": "agent-2", "event": "task.assigned", "task_id": "t1"},
        {"actor": "agent-1", "event": "task.claimed", "task_id": "t2"},
    ]
    lb = _build_leaderboard(events)
    assert len(lb) == 1
    assert lb[0]["actor"] == "agent-1"
    assert lb[0]["claims"] == 1
    assert lb[0]["closes"] == 0
    assert lb[0]["total"] == 1
