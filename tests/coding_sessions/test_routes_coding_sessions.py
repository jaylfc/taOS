"""Route tests for /api/coding-sessions."""
import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


def _make_body(**overrides):
    base = {
        "cli": "claude",
        "launch_target": "host-folder",
        "workdir": "/home/jay/myrepo",
        "repo_source": {"kind": "local", "value": "/home/jay/myrepo"},
        "alias": "my-session",
    }
    base.update(overrides)
    return base




async def _make_client(app):
    """Init stores, create an admin user, return an authenticated AsyncClient."""
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    coding_session_store = app.state.coding_session_store
    if coding_session_store._db is None:
        await coding_session_store.init()

    metrics = app.state.metrics
    if metrics._db is None:
        await metrics.init()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True
    return uid, token


@pytest.mark.asyncio
async def test_create_returns_starting_status(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions", json=_make_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"].startswith("cs-")
        assert data["status"] == "starting"
        assert data["cli"] == "claude"
        assert data["alias"] == "my-session"
        assert data["created_by"] == uid
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_invalid_cli_returns_400(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions", json=_make_body(cli="vim"))
        assert resp.status_code == 400
        assert "cli" in resp.json()["error"]
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_invalid_launch_target_returns_400(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions", json=_make_body(launch_target="docker"))
        assert resp.status_code == 400
        assert "launch_target" in resp.json()["error"]
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_worker_lxc_without_worker_returns_400(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions", json=_make_body(
            launch_target="worker-lxc", worker=None
        ))
        assert resp.status_code == 400
        assert "worker" in resp.json()["error"]
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_worker_lxc_with_worker_succeeds(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions", json=_make_body(
            launch_target="worker-lxc", worker="pi-worker-01"
        ))
        assert resp.status_code == 200
        assert resp.json()["worker"] == "pi-worker-01"
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_list_returns_created_session(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        create_resp = await c.post("/api/coding-sessions", json=_make_body())
        sid = create_resp.json()["id"]

        list_resp = await c.get("/api/coding-sessions")
        assert list_resp.status_code == 200
        ids = [s["id"] for s in list_resp.json()["items"]]
        assert sid in ids
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_get_returns_session(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        create_resp = await c.post("/api/coding-sessions", json=_make_body())
        sid = create_resp.json()["id"]
        get_resp = await c.get(f"/api/coding-sessions/{sid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == sid
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_get_missing_returns_404(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.get("/api/coding-sessions/cs-missing")
        assert resp.status_code == 404
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_non_owner_gets_404(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)

    # Create a session owned by a different user via the store directly
    store = app.state.coding_session_store
    session = await store.create_session(
        cli="claude",
        launch_target="host-folder",
        workdir="/home/other/repo",
        repo_source={"kind": "local", "value": "/home/other/repo"},
        created_by="other-user-id",
    )
    sid = session["id"]

    # The authenticated admin-but-acting-as-regular user should not see other's session
    # (admin is owner of their own sessions; this session belongs to "other-user-id")
    # To test non-owner: mint a session token with a non-admin user_id that isn't "other-user-id"
    non_owner_token = app.state.auth.create_session(user_id=uid, long_lived=True)
    # uid is the admin user. Since is_admin is True they CAN see it. Instead we
    # need to verify the route's ownership check by minting a token for a
    # non-existent user_id (the auth middleware will still accept any valid token).
    fake_token = app.state.auth.create_session(user_id="fake-user-id", long_lived=True)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": fake_token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.get(f"/api/coding-sessions/{sid}")
        assert resp.status_code == 404

    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_stop_transitions_status(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        create_resp = await c.post("/api/coding-sessions", json=_make_body())
        sid = create_resp.json()["id"]
        stop_resp = await c.post(f"/api/coding-sessions/{sid}/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "stopped"
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_archive_transitions_status(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        create_resp = await c.post("/api/coding-sessions", json=_make_body())
        sid = create_resp.json()["id"]
        archive_resp = await c.post(f"/api/coding-sessions/{sid}/archive")
        assert archive_resp.status_code == 200
        assert archive_resp.json()["status"] == "archived"
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_list_excludes_archived_by_default(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        s1 = (await c.post("/api/coding-sessions", json=_make_body(alias="active"))).json()
        s2 = (await c.post("/api/coding-sessions", json=_make_body(alias="archived"))).json()
        await c.post(f"/api/coding-sessions/{s2['id']}/archive")

        items = (await c.get("/api/coding-sessions")).json()["items"]
        ids = [s["id"] for s in items]
        assert s1["id"] in ids
        assert s2["id"] not in ids

        items_all = (await c.get("/api/coding-sessions?include_archived=true")).json()["items"]
        ids_all = [s["id"] for s in items_all]
        assert s1["id"] in ids_all
        assert s2["id"] in ids_all
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_create_registers_in_agent_registry(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions", json=_make_body(alias="reg-test"))
        assert resp.status_code == 200
        sid = resp.json()["id"]

    # The agent_registry should have an entry with handle == session id
    registry = app.state.agent_registry
    all_entries = await registry.list_all()
    matching = [e for e in all_entries if e.get("handle") == sid]
    assert len(matching) == 1
    assert matching[0]["framework"] == "coding-session"
    assert matching[0]["display_name"] == "reg-test"

    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_create_succeeds_even_if_registry_unavailable(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    # Simulate registry being unavailable
    app.state.agent_registry = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions", json=_make_body(alias="no-registry"))
        assert resp.status_code == 200
        assert resp.json()["status"] == "starting"
    await app.state.coding_session_store.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_rename_updates_alias_and_registry(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        create_resp = await c.post("/api/coding-sessions", json=_make_body(alias="old-name"))
        sid = create_resp.json()["id"]
        resp = await c.patch(f"/api/coding-sessions/{sid}", json={"alias": "new-name"})
        assert resp.status_code == 200
        assert resp.json()["alias"] == "new-name"
        # The registry entry (handle == session id) reflects the new alias.
        rows = await app.state.agent_registry.list_for_user(uid)
        match = next((r for r in rows if r.get("handle") == sid), None)
        assert match is not None
        assert match["display_name"] == "new-name"
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


class _FakeLauncher:
    """In-memory launcher for route tests: no real tmux."""

    def __init__(self):
        self.started = []
        self.stopped = []
        self._running = set()

    def is_running(self, sid):
        return sid in self._running

    def start_host_folder(self, sid, workdir, cli):
        self.started.append((sid, workdir, cli))
        self._running.add(sid)
        return f"taos-cs-{sid}"

    def stop(self, sid):
        self.stopped.append(sid)
        self._running.discard(sid)

    def capture(self, sid):
        return f"output for {sid}\n"

    def send_input(self, sid, text):
        pass


@pytest.mark.asyncio
async def test_rename_empty_alias_returns_400(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        sid = (await c.post("/api/coding-sessions", json=_make_body())).json()["id"]
        resp = await c.patch(f"/api/coding-sessions/{sid}", json={"alias": "   "})
        assert resp.status_code == 400
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_start_host_folder_runs_and_captures(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    app.state.coding_launcher = _FakeLauncher()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        sid = (await c.post("/api/coding-sessions", json=_make_body())).json()["id"]
        resp = await c.post(f"/api/coding-sessions/{sid}/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["tmux_session"] == f"taos-cs-{sid}"
        assert (sid, "/home/jay/myrepo", "claude") in app.state.coding_launcher.started
        # The transcript captured the initial output.
        tr = await c.get(f"/api/coding-sessions/{sid}/transcript")
        assert tr.status_code == 200
        assert any(f"output for {sid}" in e["text"] for e in tr.json()["entries"])
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_rename_missing_session_returns_404(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.patch("/api/coding-sessions/cs-does-not-exist", json={"alias": "x"})
        assert resp.status_code == 404
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_start_non_host_folder_returns_501(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    app.state.coding_launcher = _FakeLauncher()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        body = _make_body(launch_target="worker-lxc", worker="linstation")
        sid = (await c.post("/api/coding-sessions", json=body)).json()["id"]
        resp = await c.post(f"/api/coding-sessions/{sid}/start")
        assert resp.status_code == 501
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_stop_kills_tmux_and_records_status(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    app.state.coding_launcher = _FakeLauncher()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        sid = (await c.post("/api/coding-sessions", json=_make_body())).json()["id"]
        await c.post(f"/api/coding-sessions/{sid}/start")
        resp = await c.post(f"/api/coding-sessions/{sid}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"
        assert sid in app.state.coding_launcher.stopped
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_stop_does_not_resurrect_archived_session(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    app.state.coding_launcher = _FakeLauncher()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        sid = (await c.post("/api/coding-sessions", json=_make_body())).json()["id"]
        await c.post(f"/api/coding-sessions/{sid}/archive")
        resp = await c.post(f"/api/coding-sessions/{sid}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
        assert sid not in app.state.coding_launcher.stopped
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()


@pytest.mark.asyncio
async def test_start_missing_session_returns_404(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    uid, token = await _make_client(app)
    app.state.coding_launcher = _FakeLauncher()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies={"taos_session": token}, event_hooks=csrf_event_hooks()) as c:
        resp = await c.post("/api/coding-sessions/cs-nope/start")
        assert resp.status_code == 404
    await app.state.coding_session_store.close()
    await app.state.agent_registry.close()
    await app.state.metrics.close()
