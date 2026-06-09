"""Tests for per-user ownership scoping on the projects stores and routes.

Covers:
  - Store: create_project binds caller's user_id; list_for_user returns only own
  - Routes: create binds user_id from auth; member sees only own projects/tasks
  - Routes: non-owner gets 404 on read; 403 on mutate
  - Routes: admin sees/manages all
  - Legacy rows (user_id='') are visible to admin only
"""
from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.auth_context import CurrentUser, require_owner_or_admin


# ---------------------------------------------------------------------------
# Store-level ownership tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestProjectStoreOwnership:

    async def _make_store(self, db_path):
        s = ProjectStore(db_path)
        await s.init()
        return s

    async def test_create_project_stores_user_id(self, tmp_path):
        store = await self._make_store(tmp_path / "p.db")
        try:
            p = await store.create_project(
                name="Alpha", slug="alpha", created_by="user-1", user_id="user-1"
            )
            assert p["user_id"] == "user-1"
            again = await store.get_project(p["id"])
            assert again["user_id"] == "user-1"
        finally:
            await store.close()

    async def test_create_project_default_user_id_is_empty(self, tmp_path):
        store = await self._make_store(tmp_path / "p.db")
        try:
            p = await store.create_project(name="Beta", slug="beta", created_by="u")
            assert p["user_id"] == ""
        finally:
            await store.close()

    async def test_list_for_user_returns_only_own(self, tmp_path):
        store = await self._make_store(tmp_path / "p.db")
        try:
            await store.create_project(name="A", slug="a", created_by="alice", user_id="alice")
            await store.create_project(name="B", slug="b", created_by="alice", user_id="alice")
            await store.create_project(name="C", slug="c", created_by="bob", user_id="bob")
            await store.create_project(name="Legacy", slug="leg", created_by="u")  # user_id=''

            alice_projects = await store.list_for_user("alice")
            bob_projects = await store.list_for_user("bob")
            nobody_projects = await store.list_for_user("nobody")

            assert len(alice_projects) == 2
            assert all(p["user_id"] == "alice" for p in alice_projects)
            assert len(bob_projects) == 1
            assert bob_projects[0]["user_id"] == "bob"
            assert len(nobody_projects) == 0
        finally:
            await store.close()

    async def test_list_for_user_respects_status_filter(self, tmp_path):
        store = await self._make_store(tmp_path / "p.db")
        try:
            active = await store.create_project(name="A", slug="a", created_by="u", user_id="u")
            archived = await store.create_project(name="B", slug="b", created_by="u", user_id="u")
            await store.set_status(archived["id"], "archived")

            active_list = await store.list_for_user("u", status="active")
            archived_list = await store.list_for_user("u", status="archived")
            all_list = await store.list_for_user("u", status=None)

            assert [p["id"] for p in active_list] == [active["id"]]
            assert [p["id"] for p in archived_list] == [archived["id"]]
            assert len(all_list) == 2
        finally:
            await store.close()

    async def test_list_projects_includes_all(self, tmp_path):
        """list_projects (admin view) returns all projects regardless of user_id."""
        store = await self._make_store(tmp_path / "p.db")
        try:
            await store.create_project(name="A", slug="a", created_by="u1", user_id="u1")
            await store.create_project(name="B", slug="b", created_by="u2", user_id="u2")
            await store.create_project(name="Leg", slug="leg", created_by="u")  # user_id=''
            all_projects = await store.list_projects(status=None)
            assert len(all_projects) == 3
        finally:
            await store.close()

    async def test_legacy_row_not_in_list_for_user(self, tmp_path):
        """Legacy rows (user_id='') must NOT appear in list_for_user('') — empty string is not a valid user."""
        store = await self._make_store(tmp_path / "p.db")
        try:
            await store.create_project(name="Legacy", slug="leg", created_by="u")
            items = await store.list_for_user("")
            assert items == []
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# require_owner_or_admin unit tests
# ---------------------------------------------------------------------------

class TestRequireOwnerOrAdmin:

    def test_owner_passes(self):
        user = CurrentUser(user_id="alice", is_admin=False)
        require_owner_or_admin(user, "alice")  # no exception

    def test_admin_passes_for_any_resource(self):
        user = CurrentUser(user_id="admin-1", is_admin=True)
        require_owner_or_admin(user, "bob")  # no exception

    def test_non_owner_raises_403(self):
        from fastapi import HTTPException
        user = CurrentUser(user_id="alice", is_admin=False)
        with pytest.raises(HTTPException) as exc_info:
            require_owner_or_admin(user, "bob")
        assert exc_info.value.status_code == 403

    def test_admin_passes_for_legacy_empty_user_id(self):
        user = CurrentUser(user_id="admin-1", is_admin=True)
        require_owner_or_admin(user, "")  # admin can manage legacy rows


# ---------------------------------------------------------------------------
# Route fixtures — admin client + member client
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


def _init_stores_and_admin(app) -> str:
    """Set up admin user, return admin user_id. Idempotent."""
    # setup_user raises if a user already exists — guard for shared fixtures
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    return record["id"] if record else ""


async def _init_project_stores(app):
    for attr in ("project_store", "project_task_store"):
        store = getattr(app.state, attr, None)
        if store is not None and store._db is None:
            await store.init()
    app.state.projects_root.mkdir(parents=True, exist_ok=True)


@pytest_asyncio.fixture
async def member_client(app, tmp_data_dir):
    """Authenticated non-admin member client."""
    await _init_project_stores(app)
    _init_stores_and_admin(app)
    member_uid = _add_member_user(app, username="member", password="memberpass1")
    token = app.state.auth.create_session(user_id=member_uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        c._test_uid = member_uid
        c._test_app = app
        yield c

    await app.state.project_store.close()
    await app.state.project_task_store.close()


@pytest_asyncio.fixture
async def two_member_clients(app, tmp_data_dir):
    """Two separate non-admin member clients (alice and bob) sharing one app."""
    await _init_project_stores(app)
    _init_stores_and_admin(app)
    alice_uid = _add_member_user(app, username="alice", password="alicepass1")
    bob_uid = _add_member_user(app, username="bob", password="bobspass1")
    alice_token = app.state.auth.create_session(user_id=alice_uid, long_lived=True)
    bob_token = app.state.auth.create_session(user_id=bob_uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": alice_token},
    ) as alice_c:
        async with AsyncClient(
            transport=transport, base_url="http://test",
            cookies={"taos_session": bob_token},
        ) as bob_c:
            alice_c._test_uid = alice_uid
            alice_c._test_app = app
            bob_c._test_uid = bob_uid
            bob_c._test_app = app
            yield alice_c, bob_c

    await app.state.project_store.close()
    await app.state.project_task_store.close()


# ---------------------------------------------------------------------------
# Route ownership tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_binds_caller_user_id(member_client):
    """Creating a project via the route binds the authenticated user's id."""
    resp = await member_client.post(
        "/api/projects",
        json={"name": "My Project", "slug": "my-project"},
    )
    assert resp.status_code == 200
    p = resp.json()
    assert p["user_id"] == member_client._test_uid


@pytest.mark.asyncio
async def test_member_list_sees_only_own_projects(two_member_clients):
    """A member only sees their own projects in the list."""
    alice, bob = two_member_clients
    await alice.post("/api/projects", json={"name": "Alice Project", "slug": "alice-proj"})
    await bob.post("/api/projects", json={"name": "Bob Project", "slug": "bob-proj"})

    alice_resp = await alice.get("/api/projects")
    bob_resp = await bob.get("/api/projects")

    assert alice_resp.status_code == 200
    assert bob_resp.status_code == 200
    alice_slugs = {p["slug"] for p in alice_resp.json()["items"]}
    bob_slugs = {p["slug"] for p in bob_resp.json()["items"]}
    assert "alice-proj" in alice_slugs
    assert "bob-proj" not in alice_slugs
    assert "bob-proj" in bob_slugs
    assert "alice-proj" not in bob_slugs


@pytest.mark.asyncio
async def test_non_owner_get_project_returns_404(two_member_clients):
    """A non-owner getting a single project gets existence-hiding 404."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "a-secret"})
    pid = resp.json()["id"]

    # Bob cannot see Alice's project
    resp = await bob.get(f"/api/projects/{pid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_can_get_own_project(member_client):
    """The owner can retrieve their own project."""
    resp = await member_client.post(
        "/api/projects", json={"name": "Mine", "slug": "mine"}
    )
    pid = resp.json()["id"]
    resp = await member_client.get(f"/api/projects/{pid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pid


@pytest.mark.asyncio
async def test_non_owner_update_returns_403(two_member_clients):
    """A non-owner patching another user's project gets 403."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "a-upd"})
    pid = resp.json()["id"]

    resp = await bob.patch(f"/api/projects/{pid}", json={"name": "Hijacked"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_owner_delete_returns_403(two_member_clients):
    """A non-owner deleting another user's project gets 403."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "a-del"})
    pid = resp.json()["id"]

    resp = await bob.delete(f"/api/projects/{pid}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_owner_archive_returns_403(two_member_clients):
    """A non-owner archiving another user's project gets 403."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "a-arch"})
    pid = resp.json()["id"]

    resp = await bob.post(f"/api/projects/{pid}/archive")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_sees_all_projects(two_member_clients):
    """Admin list returns all projects including other users'."""
    alice, bob = two_member_clients
    await alice.post("/api/projects", json={"name": "A", "slug": "adm-a"})
    await bob.post("/api/projects", json={"name": "B", "slug": "adm-b"})

    # Create an admin client on the same app (admin was set up in two_member_clients)
    app = alice._test_app
    admin_record = app.state.auth.find_user("admin")
    admin_uid = admin_record["id"]
    admin_token = app.state.auth.create_session(user_id=admin_uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": admin_token},
    ) as admin_c:
        # default status=active; alice and bob's projects are active
        resp = await admin_c.get("/api/projects")
        assert resp.status_code == 200
        slugs = {p["slug"] for p in resp.json()["items"]}
        assert "adm-a" in slugs
        assert "adm-b" in slugs


@pytest.mark.asyncio
async def test_admin_can_get_any_project(two_member_clients):
    """Admin can fetch any user's project by id."""
    alice, _bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "Alice Private", "slug": "adm-get"})
    pid = resp.json()["id"]

    app = alice._test_app
    admin_record = app.state.auth.find_user("admin")
    admin_uid = admin_record["id"]
    admin_token = app.state.auth.create_session(user_id=admin_uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": admin_token},
    ) as admin_c:
        resp = await admin_c.get(f"/api/projects/{pid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == pid


@pytest.mark.asyncio
async def test_admin_can_update_any_project(two_member_clients):
    """Admin can update another user's project."""
    alice, _bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "adm-upd"})
    pid = resp.json()["id"]

    app = alice._test_app
    admin_record = app.state.auth.find_user("admin")
    admin_uid = admin_record["id"]
    admin_token = app.state.auth.create_session(user_id=admin_uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": admin_token},
    ) as admin_c:
        resp = await admin_c.patch(f"/api/projects/{pid}", json={"name": "Admin Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Admin Updated"


@pytest.mark.asyncio
async def test_non_owner_cannot_create_task_in_others_project(two_member_clients):
    """A non-owner cannot create tasks in another user's project (404)."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "tsk-a"})
    pid = resp.json()["id"]

    resp = await bob.post(f"/api/projects/{pid}/tasks", json={"title": "Hack"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_owner_cannot_list_tasks_in_others_project(two_member_clients):
    """A non-owner cannot list tasks in another user's project (404)."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "ltsk-a"})
    pid = resp.json()["id"]
    await alice.post(f"/api/projects/{pid}/tasks", json={"title": "Secret"})

    resp = await bob.get(f"/api/projects/{pid}/tasks")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_owner_cannot_get_task_in_others_project(two_member_clients):
    """A non-owner cannot get a specific task in another user's project (404)."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "gtsk-a"})
    pid = resp.json()["id"]
    task = (await alice.post(f"/api/projects/{pid}/tasks", json={"title": "T"})).json()

    resp = await bob.get(f"/api/projects/{pid}/tasks/{task['id']}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_owner_cannot_update_task_in_others_project(two_member_clients):
    """A non-owner cannot update tasks in another user's project (404)."""
    alice, bob = two_member_clients
    resp = await alice.post("/api/projects", json={"name": "A", "slug": "utsk-a"})
    pid = resp.json()["id"]
    task = (await alice.post(f"/api/projects/{pid}/tasks", json={"title": "T"})).json()

    resp = await bob.patch(f"/api/projects/{pid}/tasks/{task['id']}", json={"title": "Stolen"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_can_create_and_list_tasks(member_client):
    """Project owner can fully create and list tasks."""
    resp = await member_client.post("/api/projects", json={"name": "My P", "slug": "my-p"})
    pid = resp.json()["id"]

    t = (await member_client.post(
        f"/api/projects/{pid}/tasks", json={"title": "Task 1"}
    )).json()
    assert t["id"].startswith("tsk-")

    resp = await member_client.get(f"/api/projects/{pid}/tasks")
    assert resp.status_code == 200
    assert any(item["id"] == t["id"] for item in resp.json()["items"])


@pytest.mark.asyncio
async def test_legacy_row_hidden_from_members(app, tmp_data_dir):
    """Projects with user_id='' are not visible to any member — only to admin."""
    # Initialise stores
    for attr in ("metrics", "notifications", "project_store", "project_task_store"):
        store = getattr(app.state, attr, None)
        if store is not None and store._db is None:
            await store.init()
    if app.state.project_store._db is None:
        await app.state.project_store.init()
    if app.state.project_task_store._db is None:
        await app.state.project_task_store.init()
    app.state.projects_root.mkdir(parents=True, exist_ok=True)

    # Insert legacy row directly into the store (user_id='')
    pstore = app.state.project_store
    legacy = await pstore.create_project(name="Legacy", slug="legacy", created_by="old-system")
    assert legacy["user_id"] == ""

    # Set up admin + member
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    member_uid = _add_member_user(app, username="member2", password="memberpass1")
    admin_record = app.state.auth.find_user("admin")
    admin_uid = admin_record["id"]

    admin_token = app.state.auth.create_session(user_id=admin_uid, long_lived=True)
    member_token = app.state.auth.create_session(user_id=member_uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": member_token},
    ) as member_c:
        # Member cannot see legacy row in list (legacy rows have status='active')
        resp = await member_c.get("/api/projects")
        slugs = {p["slug"] for p in resp.json()["items"]}
        assert "legacy" not in slugs

        # Member cannot get legacy row by id (existence-hiding 404)
        resp = await member_c.get(f"/api/projects/{legacy['id']}")
        assert resp.status_code == 404

    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": admin_token},
    ) as admin_c:
        # Admin CAN see legacy row in list (legacy rows have status='active')
        resp = await admin_c.get("/api/projects")
        slugs = {p["slug"] for p in resp.json()["items"]}
        assert "legacy" in slugs

        # Admin CAN get legacy row by id
        resp = await admin_c.get(f"/api/projects/{legacy['id']}")
        assert resp.status_code == 200

    await pstore.close()
    await app.state.project_task_store.close()
