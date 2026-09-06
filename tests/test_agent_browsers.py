from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path

from tinyagentos.agent_browsers import AgentBrowsersManager


@pytest_asyncio.fixture
async def mgr(tmp_path):
    m = AgentBrowsersManager(db_path=tmp_path / "browsers.db", mock=True)
    await m.init()
    yield m
    await m.close()


@pytest.mark.asyncio
async def test_create_profile(mgr):
    profile = await mgr.create_profile("my-profile", agent_name="agent1", node="local")
    assert profile["profile_name"] == "my-profile"
    assert profile["agent_name"] == "agent1"
    assert profile["node"] == "local"
    assert profile["status"] == "stopped"
    assert profile["container_id"] is None
    assert "id" in profile
    assert "created_at" in profile
    assert "updated_at" in profile


@pytest.mark.asyncio
async def test_list_profiles(mgr):
    await mgr.create_profile("profile-a", agent_name="agent1")
    await mgr.create_profile("profile-b", agent_name="agent2")
    await mgr.create_profile("profile-c", agent_name="agent1")

    all_profiles = await mgr.list_profiles()
    assert len(all_profiles) == 3

    agent1_profiles = await mgr.list_profiles(agent_name="agent1")
    assert len(agent1_profiles) == 2
    assert all(p["agent_name"] == "agent1" for p in agent1_profiles)

    agent2_profiles = await mgr.list_profiles(agent_name="agent2")
    assert len(agent2_profiles) == 1


@pytest.mark.asyncio
async def test_start_stop_browser(mgr):
    profile = await mgr.create_profile("test-profile")
    pid = profile["id"]

    started = await mgr.start_browser(pid)
    assert started is True

    updated = await mgr.get_profile(pid)
    assert updated["status"] == "running"
    assert updated["container_id"] is not None

    stopped = await mgr.stop_browser(pid)
    assert stopped is True

    updated2 = await mgr.get_profile(pid)
    assert updated2["status"] == "stopped"


@pytest.mark.asyncio
async def test_one_active_per_agent(mgr):
    p1 = await mgr.create_profile("profile-1", agent_name="agentX")
    p2 = await mgr.create_profile("profile-2", agent_name="agentX")

    await mgr.start_browser(p1["id"])
    p1_running = await mgr.get_profile(p1["id"])
    assert p1_running["status"] == "running"

    # Starting second should stop first
    await mgr.start_browser(p2["id"])

    p1_after = await mgr.get_profile(p1["id"])
    p2_after = await mgr.get_profile(p2["id"])
    assert p1_after["status"] == "stopped"
    assert p2_after["status"] == "running"


@pytest.mark.asyncio
async def test_delete_profile(mgr):
    profile = await mgr.create_profile("to-delete")
    pid = profile["id"]

    deleted = await mgr.delete_profile(pid)
    assert deleted is True

    fetched = await mgr.get_profile(pid)
    assert fetched is None

    # Deleting again returns False
    deleted2 = await mgr.delete_profile(pid)
    assert deleted2 is False


@pytest.mark.asyncio
async def test_assign_agent(mgr):
    profile = await mgr.create_profile("unassigned-profile")
    pid = profile["id"]
    assert profile["agent_name"] is None

    result = await mgr.assign_agent(pid, "new-agent")
    assert result is True

    updated = await mgr.get_profile(pid)
    assert updated["agent_name"] == "new-agent"


@pytest.mark.asyncio
async def test_move_to_node(mgr):
    profile = await mgr.create_profile("node-profile", node="local")
    pid = profile["id"]

    result = await mgr.move_to_node(pid, "node-2")
    assert result is True

    updated = await mgr.get_profile(pid)
    assert updated["node"] == "node-2"


@pytest.mark.asyncio
async def test_move_to_node_stops_running(mgr):
    profile = await mgr.create_profile("running-profile", node="local")
    pid = profile["id"]
    await mgr.start_browser(pid)

    result = await mgr.move_to_node(pid, "remote-node")
    assert result is True

    updated = await mgr.get_profile(pid)
    assert updated["node"] == "remote-node"
    assert updated["status"] == "stopped"


@pytest.mark.asyncio
async def test_get_login_status_mock(mgr):
    profile = await mgr.create_profile("login-profile")
    pid = profile["id"]

    status = await mgr.get_login_status(pid)
    assert isinstance(status, dict)
    assert "x" in status
    assert "github" in status
    assert "youtube" in status
    assert "reddit" in status
    assert all(isinstance(v, bool) for v in status.values())


@pytest.mark.asyncio
async def test_get_cookies_mock(mgr):
    profile = await mgr.create_profile("cookie-profile")
    pid = profile["id"]

    cookies = await mgr.get_cookies(pid, domain="github.com")
    assert isinstance(cookies, list)
    assert len(cookies) == 0


@pytest.mark.asyncio
async def test_get_profile_not_found(mgr):
    result = await mgr.get_profile("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_delete_profile_data(mgr):
    profile = await mgr.create_profile("data-profile")
    pid = profile["id"]
    # In mock mode, delete_profile_data should return True without error
    result = await mgr.delete_profile_data(pid)
    assert result is True
