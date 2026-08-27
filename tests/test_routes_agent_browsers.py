from __future__ import annotations

import pytest
import pytest_asyncio
import yaml
from pathlib import Path
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.agent_browsers import AgentBrowsersManager
from taos_test_csrf import csrf_event_hooks


@pytest_asyncio.fixture
async def browsers_client(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    # Init required stores
    await app.state.metrics.init()
    await app.state.notifications.init()
    await app.state.qmd_client.init()
    await app.state.knowledge_store.init()

    # Wire a mock AgentBrowsersManager onto app state
    browsers_mgr = AgentBrowsersManager(db_path=tmp_path / "browsers.db", mock=True)
    await browsers_mgr.init()
    app.state.agent_browsers = browsers_mgr

    # Register the agent-browsers router (not yet added in app.py)
    from tinyagentos.routes.agent_browsers import router as agent_browsers_router
    app.include_router(agent_browsers_router)

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = app.state.auth.find_user("admin")
    _token = app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}, event_hooks=csrf_event_hooks()) as c:
        yield c

    await browsers_mgr.close()
    await app.state.knowledge_store.close()
    await app.state.notifications.close()
    await app.state.metrics.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()


# ------------------------------------------------------------------
# List & create
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_profiles_empty(browsers_client):
    resp = await browsers_client.get("/api/agent-browsers/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert "profiles" in data
    assert isinstance(data["profiles"], list)
    assert len(data["profiles"]) == 0


@pytest.mark.asyncio
async def test_create_profile(browsers_client):
    resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "test-profile",
        "agent_name": "agent1",
        "node": "local",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_name"] == "test-profile"
    assert data["agent_name"] == "agent1"
    assert data["status"] == "stopped"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_profiles_with_filter(browsers_client):
    await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "profile-a", "agent_name": "agentA"
    })
    await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "profile-b", "agent_name": "agentB"
    })

    resp = await browsers_client.get("/api/agent-browsers/profiles?agent_name=agentA")
    assert resp.status_code == 200
    profiles = resp.json()["profiles"]
    assert len(profiles) == 1
    assert profiles[0]["agent_name"] == "agentA"


# ------------------------------------------------------------------
# Get single profile
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_profile(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "single-profile"
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.get(f"/api/agent-browsers/profiles/{pid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pid


@pytest.mark.asyncio
async def test_get_profile_not_found(browsers_client):
    resp = await browsers_client.get("/api/agent-browsers/profiles/does-not-exist")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_profile(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "to-delete"
    })
    pid = create_resp.json()["id"]

    del_resp = await browsers_client.delete(f"/api/agent-browsers/profiles/{pid}")
    assert del_resp.status_code == 200

    get_resp = await browsers_client.get(f"/api/agent-browsers/profiles/{pid}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_profile_not_found(browsers_client):
    resp = await browsers_client.delete("/api/agent-browsers/profiles/no-such-profile")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_profile_data(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "data-profile"
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.delete(f"/api/agent-browsers/profiles/{pid}/data")
    assert resp.status_code == 200


# ------------------------------------------------------------------
# Start / Stop
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_browser(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "startable"
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.post(f"/api/agent-browsers/profiles/{pid}/start")
    assert resp.status_code == 200

    profile = (await browsers_client.get(f"/api/agent-browsers/profiles/{pid}")).json()
    assert profile["status"] == "running"


@pytest.mark.asyncio
async def test_stop_browser(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "stoppable"
    })
    pid = create_resp.json()["id"]
    await browsers_client.post(f"/api/agent-browsers/profiles/{pid}/start")

    resp = await browsers_client.post(f"/api/agent-browsers/profiles/{pid}/stop")
    assert resp.status_code == 200

    profile = (await browsers_client.get(f"/api/agent-browsers/profiles/{pid}")).json()
    assert profile["status"] == "stopped"


# ------------------------------------------------------------------
# Screenshot
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_screenshot_mock(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "screenshot-profile"
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.get(f"/api/agent-browsers/profiles/{pid}/screenshot")
    # Mock manager returns a PNG stub even for stopped profiles
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


# ------------------------------------------------------------------
# Cookies & login status
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cookies_mock(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "cookie-profile",
        "agent_name": "agent-cookie",
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.get(
        f"/api/agent-browsers/agent-cookie/{pid}/cookies?domain=github.com"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "cookies" in data
    assert data["cookies"] == []


@pytest.mark.asyncio
async def test_login_status_mock(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "login-profile"
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.get(f"/api/agent-browsers/profiles/{pid}/login-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "x" in data
    assert "github" in data
    assert "youtube" in data
    assert "reddit" in data


# ------------------------------------------------------------------
# Assign & move
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_agent(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "assign-profile"
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.put(
        f"/api/agent-browsers/profiles/{pid}/assign",
        json={"agent_name": "new-agent"},
    )
    assert resp.status_code == 200

    profile = (await browsers_client.get(f"/api/agent-browsers/profiles/{pid}")).json()
    assert profile["agent_name"] == "new-agent"


@pytest.mark.asyncio
async def test_move_to_node(browsers_client):
    create_resp = await browsers_client.post("/api/agent-browsers/profiles", json={
        "profile_name": "move-profile", "node": "local"
    })
    pid = create_resp.json()["id"]

    resp = await browsers_client.put(
        f"/api/agent-browsers/profiles/{pid}/move",
        json={"node": "remote-node"},
    )
    assert resp.status_code == 200

    profile = (await browsers_client.get(f"/api/agent-browsers/profiles/{pid}")).json()
    assert profile["node"] == "remote-node"
