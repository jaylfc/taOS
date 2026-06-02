from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path

from tinyagentos.browser_sessions import BrowserSessionManager


@pytest_asyncio.fixture
async def mgr(tmp_path):
    m = BrowserSessionManager(db_path=tmp_path / "browser_sessions.db", mock=True)
    await m.init()
    yield m
    await m.close()


@pytest.mark.asyncio
async def test_create_and_get_session_roundtrip(mgr):
    session = await mgr.create_session(
        owner_type="user",
        owner_id="user-1",
        url="https://example.com",
        profile_name="default",
    )
    assert session["owner_type"] == "user"
    assert session["owner_id"] == "user-1"
    assert session["url"] == "https://example.com"
    assert session["profile_name"] == "default"
    assert session["status"] == "pending"
    assert "id" in session
    assert "created_at" in session
    assert "updated_at" in session
    assert "last_active" in session

    fetched = await mgr.get_session(session["id"])
    assert fetched == session


@pytest.mark.asyncio
async def test_list_sessions_filters_by_owner(mgr):
    await mgr.create_session("user", "user-1", "https://a.com")
    await mgr.create_session("user", "user-1", "https://b.com")
    await mgr.create_session("agent", "agent-42", "https://c.com")

    user1_sessions = await mgr.list_sessions("user", "user-1")
    assert len(user1_sessions) == 2
    assert all(s["owner_type"] == "user" and s["owner_id"] == "user-1" for s in user1_sessions)

    agent_sessions = await mgr.list_sessions("agent", "agent-42")
    assert len(agent_sessions) == 1
    assert agent_sessions[0]["owner_type"] == "agent"

    nobody = await mgr.list_sessions("user", "nobody")
    assert nobody == []


@pytest.mark.asyncio
async def test_mark_running_sets_fields(mgr):
    session = await mgr.create_session("agent", "agent-1", "https://work.com")
    sid = session["id"]

    await mgr.mark_running(
        sid,
        node="node-1",
        container_id="ctr-abc",
        neko_url="http://neko:8080",
        cdp_url="ws://cdp:9222",
    )

    updated = await mgr.get_session(sid)
    assert updated["status"] == "running"
    assert updated["node"] == "node-1"
    assert updated["container_id"] == "ctr-abc"
    assert updated["neko_url"] == "http://neko:8080"
    assert updated["cdp_url"] == "ws://cdp:9222"


@pytest.mark.asyncio
async def test_touch_active_updates_last_active(mgr):
    t0 = 1_000_000.0
    session = await mgr.create_session("user", "user-2", "https://touch.com", now=t0)
    sid = session["id"]

    assert session["last_active"] == t0

    t1 = t0 + 60.0
    await mgr.touch_active(sid, now=t1)

    updated = await mgr.get_session(sid)
    assert updated["last_active"] == t1
    assert updated["updated_at"] == t1


@pytest.mark.asyncio
async def test_terminate_session(mgr):
    session = await mgr.create_session("user", "user-3", "https://stop.com")
    sid = session["id"]

    result = await mgr.terminate_session(sid)
    assert result is True

    stopped = await mgr.get_session(sid)
    assert stopped["status"] == "stopped"

    # Unknown id returns False
    result2 = await mgr.terminate_session("does-not-exist")
    assert result2 is False
