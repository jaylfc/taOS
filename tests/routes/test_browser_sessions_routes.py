"""Tests for /api/browser/sessions routes (Task 4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.browser_sessions import BrowserSessionManager, BrowserWorkerError


# ---------------------------------------------------------------------------
# Stub cluster manager
# ---------------------------------------------------------------------------

@dataclass
class _StubWorker:
    name: str
    status: str = "online"
    hardware: dict = field(default_factory=dict)
    load: float = 0.0
    capabilities: list[str] = field(default_factory=lambda: ["browser"])
    url: str = "http://worker.example:7080"


class _StubCluster:
    def __init__(self, workers: list[_StubWorker]):
        self._workers = workers

    def get_workers(self) -> list[_StubWorker]:
        return self._workers

    def get_worker(self, name: str) -> _StubWorker | None:
        for w in self._workers:
            if w.name == name:
                return w
        return None


def _capable_worker() -> _StubWorker:
    return _StubWorker(
        name="node-1",
        status="online",
        hardware={"ram_mb": 8192, "cpu": {"cores": 8}},
        load=0.2,
    )


def _no_cluster() -> _StubCluster:
    return _StubCluster(workers=[])


def _capable_cluster() -> _StubCluster:
    return _StubCluster(workers=[_capable_worker()])


def _make_mock_httpx_client(status_code: int, json_body: dict):
    """Return a patched httpx.AsyncClient context manager returning a stub response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=resp)
    return mock_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(app, tmp_path):
    """Async client with browser_sessions store, signing key, and cluster stub injected."""
    bs = BrowserSessionManager(tmp_path / "bs.db", mock=True)
    await bs.init()

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _capable_cluster()

    # Set up auth
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c

    await bs.close()


@pytest_asyncio.fixture
async def client_no_node(app, tmp_path):
    """Same as client but cluster has no capable workers."""
    bs = BrowserSessionManager(tmp_path / "bs2.db", mock=True)
    await bs.init()

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _no_cluster()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c

    await bs.close()


# ---------------------------------------------------------------------------
# POST /api/browser/sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_session_unauthenticated(app, tmp_path):
    """Unauthenticated POST must return 401."""
    app.state.browser_sessions = BrowserSessionManager(tmp_path / "bs_unauth.db", mock=True)
    await app.state.browser_sessions.init()
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _capable_cluster()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert resp.status_code == 401
    await app.state.browser_sessions.close()


_WORKER_START_BODY = {
    "container_id": "ctr-test-01",
    "neko_url": "http://10.0.0.5:8800/?usr=neko&pwd=testpwd",
    "cdp_url": None,
    "http_port": 8800,
    "epr_lo": 59000,
    "epr_hi": 59009,
}


@pytest.mark.asyncio
async def test_post_session_capable_node_returns_201(client):
    """Authed POST with a capable node returns 201 and a running session."""
    mock_client = _make_mock_httpx_client(200, _WORKER_START_BODY)
    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "running"
    assert body["url"] == "https://example.com"
    assert body["container_id"] == "ctr-test-01"
    assert "id" in body


@pytest.mark.asyncio
async def test_post_session_worker_start_failure_returns_502(client):
    """When start_on_worker raises BrowserWorkerError the route returns 502."""
    mock_client = _make_mock_httpx_client(500, {"error": "docker failed"})
    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert resp.status_code == 502
    assert resp.json()["error"] == "worker_start_failed"


@pytest.mark.asyncio
async def test_post_session_specific_node_returns_201(client):
    """Authed POST with explicit node= returns 201 when node is capable."""
    mock_client = _make_mock_httpx_client(200, _WORKER_START_BODY)
    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            "/api/browser/sessions",
            json={"url": "https://example.com", "node": "node-1"},
        )
    assert resp.status_code == 201
    assert resp.json()["status"] == "running"


@pytest.mark.asyncio
async def test_post_session_specific_unknown_node_returns_409(client):
    """Authed POST with explicit node that is not capable returns 409."""
    resp = await client.post(
        "/api/browser/sessions",
        json={"url": "https://example.com", "node": "nonexistent-node"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "no_capable_node"


@pytest.mark.asyncio
async def test_post_session_no_capable_node_returns_409(client_no_node):
    """Authed POST with no capable node returns 409 no_capable_node."""
    resp = await client_no_node.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "no_capable_node"


# ---------------------------------------------------------------------------
# GET /api/browser/sessions/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_own_running_session_has_stream_token(client):
    """GET own running session returns 200 with a stream_token."""
    mock_client = _make_mock_httpx_client(200, _WORKER_START_BODY)
    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        create_resp = await client.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["id"]

    resp = await client.get(f"/api/browser/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == session_id
    assert body["status"] == "running"
    assert "stream_token" in body


@pytest.mark.asyncio
async def test_get_nonexistent_session_returns_404(client):
    """GET a session that doesn't exist returns 404."""
    resp = await client.get("/api/browser/sessions/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_other_users_session_returns_404(app, tmp_path):
    """GET another user's session returns 404 (ownership check)."""
    # Create a session owned by a different user directly via manager
    bs = BrowserSessionManager(tmp_path / "bs_other.db", mock=True)
    await bs.init()
    session = await bs.create_session("user", "other-user-id", "https://example.com")
    session_id = session["id"]

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _capable_cluster()

    # Auth as a different user (admin)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        resp = await c.get(f"/api/browser/sessions/{session_id}")

    assert resp.status_code == 404
    await bs.close()


# ---------------------------------------------------------------------------
# POST /api/browser/sessions/{id}/terminate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_terminate_own_session_returns_ok(client):
    """Terminate own running session calls stop_on_worker and returns {ok: true}."""
    start_mock = _make_mock_httpx_client(200, _WORKER_START_BODY)
    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=start_mock):
        create_resp = await client.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["id"]

    stop_mock = _make_mock_httpx_client(200, {"ok": True})
    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=stop_mock):
        resp = await client.post(f"/api/browser/sessions/{session_id}/terminate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # Verify stop was called
    stop_mock.post.assert_awaited_once()
    call_kwargs = stop_mock.post.call_args
    assert "/worker/browser/stop" in call_kwargs.args[0]


# ---------------------------------------------------------------------------
# GET /api/browser/nodes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_browser_nodes_unauthenticated(app, tmp_path):
    """Unauthenticated GET /api/browser/nodes must return 401."""
    app.state.browser_sessions = BrowserSessionManager(tmp_path / "bs_nodes.db", mock=True)
    await app.state.browser_sessions.init()
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _capable_cluster()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/browser/nodes")
    assert resp.status_code == 401
    await app.state.browser_sessions.close()


@pytest.mark.asyncio
async def test_get_browser_nodes_returns_capable_nodes(client):
    """GET /api/browser/nodes returns the list of capable nodes."""
    resp = await client.get("/api/browser/nodes")
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert len(body["nodes"]) == 1
    node = body["nodes"][0]
    assert node["name"] == "node-1"
    assert "gpu" in node
    assert "ram_mb" in node
    assert "cores" in node
    assert "load" in node


@pytest.mark.asyncio
async def test_get_browser_nodes_empty_when_no_capable_workers(client_no_node):
    """GET /api/browser/nodes returns empty list when no capable workers."""
    resp = await client_no_node.get("/api/browser/nodes")
    assert resp.status_code == 200
    assert resp.json() == {"nodes": []}


# ---------------------------------------------------------------------------
# GET /api/browser/sessions/mine  (Task 8)
# ---------------------------------------------------------------------------

_HOST_RUNNER_BODY = {
    "container_id": "host-ctr-01",
    "neko_url": "http://host:8800/?usr=neko&pwd=hostpwd",
    "cdp_url": None,
    "http_port": 8800,
    "epr_lo": 59000,
    "epr_hi": 59009,
}


def _make_mock_runner(body: dict):
    """Return a mock BrowserContainerRunner whose start() returns body."""
    runner = MagicMock()
    runner.start = AsyncMock(return_value=body)
    return runner


@pytest_asyncio.fixture
async def client_host_capable(app, tmp_path):
    """Client where host is capable (16GB) and no cluster workers are needed."""
    bs = BrowserSessionManager(tmp_path / "bs_host.db", mock=True)
    await bs.init()

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _no_cluster()
    # host has 16 GB RAM — passes HOST_MIN_RAM_MB (6144 MB) gate
    app.state.host_hardware = {"ram_mb": 16384}
    app.state.browser_container_runner = _make_mock_runner(_HOST_RUNNER_BODY)

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c

    await bs.close()


@pytest_asyncio.fixture
async def client_no_capable_node(app, tmp_path):
    """Client where host is not capable and no cluster workers exist."""
    bs = BrowserSessionManager(tmp_path / "bs_none.db", mock=True)
    await bs.init()

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _no_cluster()
    # host has only 4 GB RAM — below HOST_MIN_RAM_MB (6144 MB) gate
    app.state.host_hardware = {"ram_mb": 4096}
    app.state.browser_container_runner = MagicMock()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c

    await bs.close()


@pytest.mark.asyncio
async def test_get_my_session_creates_and_starts_on_host(client_host_capable):
    """GET /mine on a capable host creates a session, starts it on host, returns it."""
    resp = await client_host_capable.get("/api/browser/sessions/mine")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["container_id"] == "host-ctr-01"
    assert body["node"] == "host"
    assert "id" in body


@pytest.mark.asyncio
async def test_get_my_session_running_with_neko_url_has_stream_token(client_host_capable):
    """GET /mine when session is running + neko_url present includes a stream_token."""
    resp = await client_host_capable.get("/api/browser/sessions/mine")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body.get("neko_url") == _HOST_RUNNER_BODY["neko_url"]
    assert "stream_token" in body
    assert isinstance(body["stream_token"], str)
    assert len(body["stream_token"]) > 0


@pytest.mark.asyncio
async def test_get_my_session_no_capable_node_returns_409(client_no_capable_node):
    """GET /mine when no node is capable returns 409 no_capable_node."""
    resp = await client_no_capable_node.get("/api/browser/sessions/mine")
    assert resp.status_code == 409
    assert resp.json()["error"] == "no_capable_node"


# ---------------------------------------------------------------------------
# POST /api/browser/sessions/{id}/migrate  (Task 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_session_calls_migrate_and_returns_session(app, tmp_path):
    """POST /migrate delegates to mgr.migrate_session and returns the refreshed session."""
    bs = BrowserSessionManager(tmp_path / "bs_mig.db", mock=True)
    await bs.init()

    # Create and start a session
    session = await bs.create_session("user", "dummy-uid", "https://example.com")
    sid = session["id"]
    await bs.mark_running(sid, node="host", container_id="c1", neko_url="http://host:8800/", cdp_url=None)

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _StubCluster(workers=[
        _StubWorker(
            name="fedora-browser",
            status="online",
            hardware={"ram_mb": 16384, "cpu": {"cores": 16}, "gpu": {"cuda": True, "vram_mb": 12288}},
            url="http://fedora.example:7080",
        )
    ])

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""

    # Re-own the session under the real user id
    db = bs._assert_db()
    await db.execute("UPDATE browser_sessions SET owner_id=? WHERE id=?", (uid, sid))
    await db.commit()

    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    migrate_called_with = {}

    async def _fake_migrate(session_id, *, target, stop_source, move_volume, start_target, emit):
        migrate_called_with["session_id"] = session_id
        migrate_called_with["target"] = target
        # Simulate a successful migration
        await bs.mark_running(session_id, node=target, container_id="c2", neko_url="http://fedora:8800/", cdp_url=None)
        return await bs.get_session(session_id)

    with patch.object(bs, "migrate_session", side_effect=_fake_migrate):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": token},
        ) as c:
            resp = await c.post(
                f"/api/browser/sessions/{sid}/migrate",
                json={"target": "fedora-browser"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["node"] == "fedora-browser"
    assert migrate_called_with["session_id"] == sid
    assert migrate_called_with["target"] == "fedora-browser"
    await bs.close()


@pytest.mark.asyncio
async def test_migrate_session_unknown_session_returns_404(app, tmp_path):
    """POST /migrate for an unknown session returns 404."""
    bs = BrowserSessionManager(tmp_path / "bs_mig2.db", mock=True)
    await bs.init()

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _capable_cluster()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        resp = await c.post("/api/browser/sessions/no-such-id/migrate", json={"target": "node-1"})

    assert resp.status_code == 404
    await bs.close()


@pytest.mark.asyncio
async def test_migrate_session_unknown_target_returns_409(app, tmp_path):
    """POST /migrate with a target that isn't a capable node returns 409."""
    bs = BrowserSessionManager(tmp_path / "bs_mig3.db", mock=True)
    await bs.init()

    session = await bs.create_session("user", "dummy-uid", "https://example.com")
    sid = session["id"]
    await bs.mark_running(sid, node="host", container_id="c1", neko_url="http://host:8800/", cdp_url=None)

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _capable_cluster()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""

    db = bs._assert_db()
    await db.execute("UPDATE browser_sessions SET owner_id=? WHERE id=?", (uid, sid))
    await db.commit()

    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        resp = await c.post(
            f"/api/browser/sessions/{sid}/migrate",
            json={"target": "nonexistent-node"},
        )

    assert resp.status_code == 409
    assert resp.json()["error"] == "no_capable_node"
    await bs.close()


# ---------------------------------------------------------------------------
# GET /api/browser/sessions  (C2a — list visible sessions)
# ---------------------------------------------------------------------------

async def _authed_client(app, tmp_path, db_name: str):
    """Helper: init a BrowserSessionManager + create+return an authed (uid, client, bs) triple."""
    bs = BrowserSessionManager(tmp_path / db_name, mock=True)
    await bs.init()

    app.state.browser_sessions = bs
    app.state.browser_session_signing_key = b"0" * 32
    app.state.cluster_manager = _no_cluster()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    return uid, token, bs


@pytest.mark.asyncio
async def test_list_sessions_returns_own_and_agent_sessions(app, tmp_path):
    """GET /api/browser/sessions returns own session AND an agent session whose name
    is in config.agents, but NOT an agent session whose name is not in config.agents."""
    uid, token, bs = await _authed_client(app, tmp_path, "bs_list.db")

    # routes/conftest creates the app with agents=[]; seed one agent into config.
    app.state.config.agents.append({"name": "test-agent"})

    own_session = await bs.create_session("user", uid, "https://example.com")
    agent_in_cfg = await bs.create_session("agent", "test-agent", "https://agent.example.com")
    agent_not_in_cfg = await bs.create_session("agent", "unknown-agent", "https://other.example.com")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        resp = await c.get("/api/browser/sessions")

    assert resp.status_code == 200
    body = resp.json()
    assert "sessions" in body
    ids = {s["id"] for s in body["sessions"]}
    assert own_session["id"] in ids
    assert agent_in_cfg["id"] in ids
    assert agent_not_in_cfg["id"] not in ids

    await bs.close()


@pytest.mark.asyncio
async def test_list_sessions_excludes_stopped(app, tmp_path):
    """GET /api/browser/sessions does not return stopped sessions."""
    uid, token, bs = await _authed_client(app, tmp_path, "bs_list2.db")

    own_session = await bs.create_session("user", uid, "https://example.com")
    await bs.terminate_session(own_session["id"])

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        resp = await c.get("/api/browser/sessions")

    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["sessions"]}
    assert own_session["id"] not in ids

    await bs.close()


# ---------------------------------------------------------------------------
# GET /api/browser/sessions/{id}  — agent session ownership (C2a)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_agent_session_in_config_returns_200_with_stream_token(app, tmp_path):
    """GET /{id} for a running agent session whose agent IS in config.agents returns 200 + stream_token."""
    uid, token, bs = await _authed_client(app, tmp_path, "bs_agent_get.db")

    # routes/conftest creates the app with agents=[]; seed one agent into config.
    app.state.config.agents.append({"name": "test-agent"})

    session = await bs.create_session("agent", "test-agent", "https://agent.example.com")
    sid = session["id"]
    await bs.mark_running(
        sid,
        node="node-1",
        container_id="ctr-agent-01",
        neko_url="http://10.0.0.5:8800/?usr=neko&pwd=pwd",
        cdp_url=None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        resp = await c.get(f"/api/browser/sessions/{sid}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert body["status"] == "running"
    assert "stream_token" in body

    await bs.close()


@pytest.mark.asyncio
async def test_get_agent_session_not_in_config_returns_404(app, tmp_path):
    """GET /{id} for an agent session whose agent is NOT in config.agents returns 404."""
    uid, token, bs = await _authed_client(app, tmp_path, "bs_agent_404.db")

    # "unknown-agent" is not in config.agents
    session = await bs.create_session("agent", "unknown-agent", "https://agent.example.com")
    sid = session["id"]
    await bs.mark_running(
        sid,
        node="node-1",
        container_id="ctr-unknown-01",
        neko_url="http://10.0.0.5:8800/?usr=neko&pwd=pwd",
        cdp_url=None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        resp = await c.get(f"/api/browser/sessions/{sid}")

    assert resp.status_code == 404

    await bs.close()
