"""Tests for the project routines routes: CRUD, ownership, manual trigger,
and the inbound webhook trigger."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_create_cron_routine(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Nightly", "cron_expr": "0 3 * * *", "trigger_kind": "cron"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"].startswith("rtn-")
    assert body["project_id"] == pid
    assert body["trigger_kind"] == "cron"
    assert body["next_fire"] is not None
    assert body["webhook_token"] is None


@pytest.mark.asyncio
async def test_create_cron_routine_missing_cron_expr_400(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Bad", "trigger_kind": "cron"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_cron_routine_invalid_cron_expr_400(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Bad", "trigger_kind": "cron", "cron_expr": "not a cron"},
    )
    assert resp.status_code == 400
    assert "cron" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_update_routine_invalid_cron_expr_400(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    rid = (await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Good", "trigger_kind": "cron", "cron_expr": "0 3 * * *"},
    )).json()["id"]
    resp = await client.patch(
        f"/api/projects/{pid}/routines/{rid}",
        json={"cron_expr": "0 99 * * *"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_webhook_routine_returns_token(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Hook", "trigger_kind": "webhook"},
    )
    assert resp.status_code == 200
    assert resp.json()["webhook_token"]


@pytest.mark.asyncio
async def test_create_routine_unknown_project_404(client):
    resp = await client.post(
        "/api/projects/prj-doesnotexist/routines",
        json={"title": "X", "trigger_kind": "api"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_routines(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    await client.post(f"/api/projects/{pid}/routines", json={"title": "One", "trigger_kind": "api"})
    await client.post(f"/api/projects/{pid}/routines", json={"title": "Two", "trigger_kind": "api"})
    resp = await client.get(f"/api/projects/{pid}/routines")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert {i["title"] for i in items} == {"One", "Two"}


@pytest.mark.asyncio
async def test_update_routine(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    rid = (await client.post(
        f"/api/projects/{pid}/routines", json={"title": "Original", "trigger_kind": "api"}
    )).json()["id"]
    resp = await client.patch(
        f"/api/projects/{pid}/routines/{rid}",
        json={"title": "Renamed", "enabled": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Renamed"
    assert body["enabled"] == 0


@pytest.mark.asyncio
async def test_update_routine_unknown_404(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.patch(f"/api/projects/{pid}/routines/rtn-nope", json={"title": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_routine_wrong_project_404(client):
    pid_a = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    pid_b = (await client.post("/api/projects", json={"name": "B", "slug": "b"})).json()["id"]
    rid = (await client.post(
        f"/api/projects/{pid_a}/routines", json={"title": "Original", "trigger_kind": "api"}
    )).json()["id"]
    resp = await client.patch(f"/api/projects/{pid_b}/routines/{rid}", json={"title": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_routine(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    rid = (await client.post(
        f"/api/projects/{pid}/routines", json={"title": "Gone", "trigger_kind": "api"}
    )).json()["id"]
    resp = await client.delete(f"/api/projects/{pid}/routines/{rid}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    resp = await client.get(f"/api/projects/{pid}/routines")
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_manual_trigger_creates_task(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    rid = (await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Manual fire", "body_template": "do it", "trigger_kind": "api"},
    )).json()["id"]

    resp = await client.post(f"/api/projects/{pid}/routines/{rid}/trigger")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["task"]["title"] == "Manual fire"
    assert body["task"]["body"] == "do it"

    tasks = (await client.get(f"/api/projects/{pid}/tasks")).json()["items"]
    assert any(t["id"] == body["task"]["id"] for t in tasks)


@pytest.mark.asyncio
async def test_manual_trigger_unknown_routine_404(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.post(f"/api/projects/{pid}/routines/rtn-nope/trigger")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_webhook_trigger_creates_task(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    routine = (await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Inbound", "body_template": "hook body", "trigger_kind": "webhook"},
    )).json()
    token = routine["webhook_token"]

    resp = await client.post(f"/api/webhooks/routines/{token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["task"]["title"] == "Inbound"

    tasks = (await client.get(f"/api/projects/{pid}/tasks")).json()["items"]
    assert any(t["id"] == body["task"]["id"] for t in tasks)


@pytest.mark.asyncio
async def test_webhook_trigger_unknown_token_404(client):
    resp = await client.post("/api/webhooks/routines/not-a-real-token")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_webhook_trigger_disabled_routine_404(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    routine = (await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Inbound", "trigger_kind": "webhook"},
    )).json()
    rid = routine["id"]
    token = routine["webhook_token"]
    await client.patch(f"/api/projects/{pid}/routines/{rid}", json={"enabled": False})

    resp = await client.post(f"/api/webhooks/routines/{token}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_webhook_trigger_rate_limited_after_burst(client):
    """The unauthenticated webhook must not be spammable to mass-create tasks:
    a burst on one token eventually 429s."""
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    token = (await client.post(
        f"/api/projects/{pid}/routines",
        json={"title": "Inbound", "trigger_kind": "webhook"},
    )).json()["webhook_token"]

    statuses = []
    for _ in range(8):
        statuses.append((await client.post(f"/api/webhooks/routines/{token}")).status_code)

    # Bucket capacity is small (5) with slow refill, so a tight burst of 8 must
    # include at least one 429 while the earliest requests succeed.
    assert 200 in statuses
    assert 429 in statuses


# ---------------------------------------------------------------------------
# Ownership: a non-owner member must not see or manage another user's routines
# ---------------------------------------------------------------------------

def _add_member_user(app, username: str, password: str) -> str:
    auth = app.state.auth
    invite_code = auth.add_user_invite(username, invited_by_username="admin")
    auth.complete_invite(
        username=username,
        invite_code=invite_code,
        full_name="Member User",
        email=f"{username}@test.local",
        password=password,
    )
    return auth.find_user(username)["id"]


@pytest_asyncio.fixture
async def two_owner_clients(app, tmp_data_dir):
    """Two separate non-admin users (alice, bob) sharing one app instance."""
    for attr in ("project_store", "project_task_store", "routine_store", "board_audit"):
        store = getattr(app.state, attr, None)
        if store is not None and store._db is None:
            await store.init()
    app.state.projects_root.mkdir(parents=True, exist_ok=True)
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")

    alice_uid = _add_member_user(app, "alice", "alicepass1")
    bob_uid = _add_member_user(app, "bob", "bobspass1")
    alice_token = app.state.auth.create_session(user_id=alice_uid, long_lived=True)
    bob_token = app.state.auth.create_session(user_id=bob_uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": alice_token},
    ) as alice_c:
        async with AsyncClient(
            transport=transport, base_url="http://test", cookies={"taos_session": bob_token},
        ) as bob_c:
            yield alice_c, bob_c

    await app.state.project_store.close()
    await app.state.project_task_store.close()
    await app.state.routine_store.close()
    await app.state.board_audit.close()


@pytest.mark.asyncio
async def test_non_owner_cannot_list_or_create_routines(two_owner_clients):
    alice_c, bob_c = two_owner_clients
    pid = (await alice_c.post("/api/projects", json={"name": "Alice Proj", "slug": "alice-proj"})).json()["id"]

    resp = await bob_c.get(f"/api/projects/{pid}/routines")
    assert resp.status_code == 404

    resp = await bob_c.post(
        f"/api/projects/{pid}/routines", json={"title": "Sneaky", "trigger_kind": "api"}
    )
    assert resp.status_code == 404

    rid = (await alice_c.post(
        f"/api/projects/{pid}/routines", json={"title": "Alice routine", "trigger_kind": "api"}
    )).json()["id"]
    resp = await bob_c.post(f"/api/projects/{pid}/routines/{rid}/trigger")
    assert resp.status_code == 404
