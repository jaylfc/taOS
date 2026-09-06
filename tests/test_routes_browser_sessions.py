import pytest
from unittest.mock import AsyncMock


def _make_browser_sessions_mock():
    mock = AsyncMock()
    mock.list_visible_sessions = AsyncMock(return_value=[])
    mock.get_session = AsyncMock(return_value=None)
    mock.get_or_create_mine = AsyncMock(
        return_value={
            "id": "test-session-id",
            "owner_type": "user",
            "owner_id": "admin",
            "status": "pending",
            "profile_name": "default",
            "url": "http://example.com",
            "node": None,
            "container_id": None,
            "neko_url": None,
            "cdp_url": None,
            "is_mobile": False,
        }
    )
    return mock


def _make_running_mine_mock():
    mock = AsyncMock()
    mock.get_or_create_mine = AsyncMock(
        return_value={
            "id": "test-session-id",
            "owner_type": "user",
            "owner_id": "admin",
            "status": "running",
            "profile_name": "default",
            "url": "http://example.com",
            "node": "host",
            "container_id": "abc123",
            "neko_url": None,
            "cdp_url": None,
            "is_mobile": False,
        }
    )
    return mock


class TestBrowserSessionsReadRoutes:
    @pytest.mark.asyncio
    async def test_get_nodes_returns_200(self, client):
        resp = await client.get("/api/browser/nodes")
        assert resp.status_code == 200
        body = resp.json()
        assert "nodes" in body
        assert isinstance(body["nodes"], list)

    @pytest.mark.asyncio
    async def test_list_sessions_returns_200_with_collection(self, client, monkeypatch):
        mock_mgr = _make_browser_sessions_mock()
        app = client._transport.app  # noqa: SLF001
        app.state._state["browser_sessions"] = mock_mgr  # noqa: SLF001
        resp = await client.get("/api/browser/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)

    @pytest.mark.asyncio
    async def test_get_mine_returns_200(self, client, monkeypatch):
        mock_mgr = _make_running_mine_mock()
        app = client._transport.app  # noqa: SLF001
        app.state._state["browser_sessions"] = mock_mgr  # noqa: SLF001
        resp = await client.get("/api/browser/sessions/mine")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, client, monkeypatch):
        mock_mgr = _make_browser_sessions_mock()
        app = client._transport.app  # noqa: SLF001
        app.state._state["browser_sessions"] = mock_mgr  # noqa: SLF001
        resp = await client.get("/api/browser/sessions/unknown-id-12345")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "not_found"


class TestCreateSessionHostPlacement:
    """A RAM-capable controller host serves a streamed session itself, even with
    no Tier-2 browser workers (the 'Pi isn't capable' regression)."""

    @pytest.mark.asyncio
    async def test_create_places_on_capable_host(self, client):
        app = client._transport.app  # noqa: SLF001
        # Capable host (16GB), no browser workers.
        app.state._state["host_hardware"] = {"ram_mb": 16000}  # noqa: SLF001
        runner = AsyncMock()
        app.state._state["browser_container_runner"] = runner  # noqa: SLF001

        mgr = AsyncMock()
        mgr.create_session = AsyncMock(return_value={"id": "sess-1"})
        mgr.start_on_host = AsyncMock(
            return_value={
                "id": "sess-1", "owner_type": "user", "owner_id": "admin",
                "status": "running", "node": "host", "neko_url": "http://host:8801",
                "container_id": "c1", "cdp_url": None, "is_mobile": False,
                "profile_name": "default", "url": "http://example.com",
            }
        )
        app.state._state["browser_sessions"] = mgr  # noqa: SLF001

        resp = await client.post("/api/browser/sessions", json={"url": "http://example.com"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["node"] == "host"
        mgr.start_on_host.assert_awaited_once()
        mgr.start_on_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_409_when_no_host_and_no_workers(self, client):
        app = client._transport.app  # noqa: SLF001
        # Non-capable host (4GB), no workers -> nowhere to place.
        app.state._state["host_hardware"] = {"ram_mb": 4096}  # noqa: SLF001
        mgr = AsyncMock()
        mgr.create_session = AsyncMock(return_value={"id": "sess-2"})
        app.state._state["browser_sessions"] = mgr  # noqa: SLF001
        resp = await client.post("/api/browser/sessions", json={"url": "http://example.com"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "no_capable_node"


@pytest.mark.asyncio
async def test_create_worker_lookup_failure_leaves_no_orphan(client, monkeypatch):
    """If the chosen worker can't be resolved, 409 without creating a session row."""
    import tinyagentos.routes.browser_sessions as bs

    # Force placement onto a 'worker' that the cluster can't resolve.
    monkeypatch.setattr(bs, "resolve_browser_target", lambda *a, **k: ("worker", "ghost"))
    app = client._transport.app  # noqa: SLF001
    app.state.cluster_manager.get_worker = lambda name: None  # noqa: SLF001
    mgr = AsyncMock()
    app.state._state["browser_sessions"] = mgr  # noqa: SLF001

    resp = await client.post("/api/browser/sessions", json={"url": "http://example.com"})
    assert resp.status_code == 409
    assert resp.json()["error"] == "no_capable_node"
    mgr.create_session.assert_not_awaited()
