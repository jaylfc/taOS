import time

import pytest
import pytest_asyncio

from tinyagentos.streaming import StreamingSessionStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = StreamingSessionStore(tmp_path / "streaming.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestStreamingSessionStore:
    async def test_create_session(self, store):
        session_id = await store.create_session(
            app_id="app-1",
            agent_name="agent-alpha",
            agent_type="app-expert",
            worker_name="local",
            container_id="ct-001",
        )
        assert isinstance(session_id, str)
        assert len(session_id) == 12

    async def test_get_session(self, store):
        session_id = await store.create_session(
            app_id="app-2",
            agent_name="agent-beta",
            agent_type="app-expert",
            worker_name="local",
            container_id="ct-002",
        )
        session = await store.get_session(session_id)
        assert session is not None
        assert session["session_id"] == session_id
        assert session["app_id"] == "app-2"
        assert session["agent_name"] == "agent-beta"
        assert session["agent_type"] == "app-expert"
        assert session["worker_name"] == "local"
        assert session["container_id"] == "ct-002"
        assert session["status"] == "starting"
        assert isinstance(session["started_at"], float)
        assert isinstance(session["last_activity"], float)

    async def test_get_session_nonexistent(self, store):
        result = await store.get_session("doesnotexist")
        assert result is None

    async def test_update_status(self, store):
        session_id = await store.create_session(
            app_id="app-3",
            agent_name="agent-gamma",
            agent_type="app-expert",
            worker_name="local",
            container_id="ct-003",
        )
        await store.update_status(session_id, "running")
        session = await store.get_session(session_id)
        assert session["status"] == "running"

    async def test_list_sessions(self, store):
        id1 = await store.create_session("app-a", "agent-1", "app-expert", "local", "ct-1")
        id2 = await store.create_session("app-b", "agent-2", "app-expert", "local", "ct-2")
        await store.update_status(id2, "stopped")
        sessions = await store.list_sessions()
        ids = {s["session_id"] for s in sessions}
        assert id1 in ids
        assert id2 in ids
        assert len(sessions) == 2

    async def test_list_active_only(self, store):
        id_starting = await store.create_session("app-a", "agent-1", "app-expert", "local", "ct-1")
        id_running = await store.create_session("app-b", "agent-2", "app-expert", "local", "ct-2")
        id_paused = await store.create_session("app-c", "agent-3", "app-expert", "local", "ct-3")
        id_stopped = await store.create_session("app-d", "agent-4", "app-expert", "local", "ct-4")

        await store.update_status(id_running, "running")
        await store.update_status(id_paused, "paused")
        await store.update_status(id_stopped, "stopped")

        active = await store.list_sessions(active_only=True)
        active_ids = {s["session_id"] for s in active}
        assert id_starting in active_ids
        assert id_running in active_ids
        assert id_paused in active_ids
        assert id_stopped not in active_ids

    async def test_delete_session(self, store):
        session_id = await store.create_session(
            app_id="app-del",
            agent_name="agent-del",
            agent_type="app-expert",
            worker_name="local",
            container_id="ct-del",
        )
        deleted = await store.delete_session(session_id)
        assert deleted is True
        assert await store.get_session(session_id) is None

    async def test_delete_session_nonexistent(self, store):
        deleted = await store.delete_session("nosuchsession")
        assert deleted is False

    async def test_swap_agent(self, store):
        session_id = await store.create_session(
            app_id="app-swap",
            agent_name="agent-old",
            agent_type="app-expert",
            worker_name="local",
            container_id="ct-swap",
        )
        await store.swap_agent(session_id, "agent-new", "general")
        session = await store.get_session(session_id)
        assert session["agent_name"] == "agent-new"
        assert session["agent_type"] == "general"

    async def test_touch_activity(self, store):
        session_id = await store.create_session(
            app_id="app-touch",
            agent_name="agent-touch",
            agent_type="app-expert",
            worker_name="local",
            container_id="ct-touch",
        )
        before = await store.get_session(session_id)
        before_ts = before["last_activity"]

        # Ensure measurable time delta
        time.sleep(0.05)
        await store.touch_activity(session_id)

        after = await store.get_session(session_id)
        assert after["last_activity"] > before_ts
