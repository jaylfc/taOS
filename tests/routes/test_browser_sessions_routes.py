"""Tests for /api/browser/sessions routes (Task 4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.browser_sessions import BrowserSessionManager


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


class _StubCluster:
    def __init__(self, workers: list[_StubWorker]):
        self._workers = workers

    def get_workers(self) -> list[_StubWorker]:
        return self._workers


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


@pytest.mark.asyncio
async def test_post_session_capable_node_returns_201(client):
    """Authed POST with a capable node returns 201 and a pending session."""
    resp = await client.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["url"] == "https://example.com"
    assert "id" in body


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
async def test_get_own_pending_session_no_stream_token(client):
    """GET own pending session returns 200 with no stream_token."""
    create_resp = await client.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["id"]

    resp = await client.get(f"/api/browser/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == session_id
    assert body["status"] == "pending"
    assert "stream_token" not in body


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
    """Terminate own session returns {ok: true}."""
    create_resp = await client.post("/api/browser/sessions", json={"url": "https://example.com"})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["id"]

    resp = await client.post(f"/api/browser/sessions/{session_id}/terminate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
