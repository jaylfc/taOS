from __future__ import annotations

import json
import secrets
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.projects.dispatcher import DispatcherService, DispatcherStore
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.task_store import ProjectTaskStore


def _make_app_state(tmp_path):
    class _AppState:
        pass

    state = _AppState()
    state.data_dir = tmp_path
    state.config = type("Config", (), {"agents": []})()
    return state


def _make_config(tmp_path) -> dict:
    return {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }


@pytest_asyncio.fixture
async def dispatcher_store(tmp_path):
    s = DispatcherStore(tmp_path / "dispatcher.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def project_store(tmp_path):
    s = ProjectStore(tmp_path / "projects.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def task_store(tmp_path, project_store):
    s = ProjectTaskStore(tmp_path / "tasks.db", project_store=project_store)
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def grants_store(tmp_path):
    s = AgentGrantsStore(tmp_path / "grants.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
def app_state(tmp_path, dispatcher_store, project_store, task_store, grants_store):
    state = _make_app_state(tmp_path)
    state.dispatcher_store = dispatcher_store
    state.project_store = project_store
    state.project_task_store = task_store
    state.agent_grants = grants_store
    state.bridge_sessions = None
    state.chat_channels = None
    state.chat_messages = None
    state.config.agents = [
        {"id": "agent-1", "name": "Agent 1", "canonical_id": "agent-1"},
        {"id": "agent-2", "name": "Agent 2", "canonical_id": "agent-2"},
    ]
    return state


@pytest.mark.asyncio
async def test_disabled_by_default(dispatcher_store):
    cfg = await dispatcher_store.get_config("user-1")
    assert cfg["enabled"] is False
    assert cfg["boards"] == []
    assert cfg["eligible_agents"] == []
    assert cfg["max_concurrent_per_agent"] == 1
    assert cfg["poll_seconds"] == 30


@pytest.mark.asyncio
async def test_config_crud_persists(dispatcher_store):
    await dispatcher_store.set_config("user-1", {"enabled": True, "boards": ["b1"]})
    cfg = await dispatcher_store.get_config("user-1")
    assert cfg["enabled"] is True
    assert cfg["boards"] == ["b1"]
    assert cfg["eligible_agents"] == []
    assert cfg["max_concurrent_per_agent"] == 1
    assert cfg["poll_seconds"] == 30


@pytest.mark.asyncio
async def test_config_merge_does_not_wipe_other_fields(dispatcher_store):
    await dispatcher_store.set_config(
        "user-1", {"enabled": True, "boards": ["b1"], "eligible_agents": ["a1"]}
    )
    await dispatcher_store.set_config("user-1", {"max_concurrent_per_agent": 2})
    cfg = await dispatcher_store.get_config("user-1")
    assert cfg["enabled"] is True
    assert cfg["boards"] == ["b1"]
    assert cfg["eligible_agents"] == ["a1"]
    assert cfg["max_concurrent_per_agent"] == 2


@pytest.mark.asyncio
async def test_get_all_configs(app_state):
    await app_state.dispatcher_store.set_config("u1", {"enabled": True})
    await app_state.dispatcher_store.set_config("u2", {"enabled": False})
    configs = await app_state.dispatcher_store.get_all_configs()
    assert len(configs) == 2
    assert {c["user_id"] for c in configs} == {"u1", "u2"}


@pytest.mark.asyncio
async def test_priority_then_oldest_assignment(app_state, task_store, grants_store):
    prj_a = await app_state.project_store.create_project(
        name="A", slug="proj-a", created_by="user-1", user_id="user-1"
    )
    prj_b = await app_state.project_store.create_project(
        name="B", slug="proj-b", created_by="user-1", user_id="user-1"
    )

    t1 = await task_store.create_task(
        project_id=prj_a["id"], title="Low priority old", created_by="user-1", priority=1
    )
    t2 = await task_store.create_task(
        project_id=prj_a["id"], title="High priority new", created_by="user-1", priority=10
    )
    t3 = await task_store.create_task(
        project_id=prj_b["id"], title="High priority old", created_by="user-1", priority=10
    )

    for t in [t1, t2, t3]:
        await task_store.update_task(t["id"], labels=["claimable"])

    await grants_store.add_grant(
        canonical_id="agent-1", scope="project_tasks", project_id=prj_a["id"]
    )
    await grants_store.add_grant(
        canonical_id="agent-1", scope="project_tasks", project_id=prj_b["id"]
    )
    await grants_store.add_grant(
        canonical_id="agent-2", scope="project_tasks", project_id=prj_a["id"]
    )
    await grants_store.add_grant(
        canonical_id="agent-2", scope="project_tasks", project_id=prj_b["id"]
    )

    await app_state.dispatcher_store.set_config(
        "user-1",
        {
            "enabled": True,
            "boards": [prj_a["id"], prj_b["id"]],
            "eligible_agents": ["agent-1", "agent-2"],
            "max_concurrent_per_agent": 1,
        },
    )

    service = DispatcherService(app_state)
    await service.dispatch_once()

    tasks = {t["id"]: await task_store.get_task(t["id"]) for t in [t1, t2, t3]}
    assigned = {tid: t["assignee_id"] for tid, t in tasks.items() if t["assignee_id"]}

    assert len(assigned) == 2
    assert tasks[t2["id"]]["assignee_id"] is not None
    assert tasks[t3["id"]]["assignee_id"] is not None
    assert tasks[t1["id"]]["assignee_id"] is None


@pytest.mark.asyncio
async def test_per_agent_concurrency_cap(app_state, task_store, grants_store):
    prj = await app_state.project_store.create_project(
        name="A", slug="proj-a", created_by="user-1", user_id="user-1"
    )

    t1 = await task_store.create_task(
        project_id=prj["id"], title="T1", created_by="user-1", priority=10
    )
    t2 = await task_store.create_task(
        project_id=prj["id"], title="T2", created_by="user-1", priority=9
    )
    t3 = await task_store.create_task(
        project_id=prj["id"], title="T3", created_by="user-1", priority=8
    )

    for t in [t1, t2, t3]:
        await task_store.update_task(t["id"], labels=["claimable"])

    await grants_store.add_grant(
        canonical_id="agent-1", scope="project_tasks", project_id=prj["id"]
    )

    await app_state.dispatcher_store.set_config(
        "user-1",
        {
            "enabled": True,
            "boards": [prj["id"]],
            "eligible_agents": ["agent-1"],
            "max_concurrent_per_agent": 1,
        },
    )

    service = DispatcherService(app_state)
    await service.dispatch_once()

    tasks = {t["id"]: await task_store.get_task(t["id"]) for t in [t1, t2, t3]}
    assigned = [t for t in tasks.values() if t["assignee_id"]]
    assert len(assigned) == 1
    assert assigned[0]["assignee_id"] == "agent-1"


@pytest.mark.asyncio
async def test_blocked_on_skip(app_state, task_store, grants_store):
    prj = await app_state.project_store.create_project(
        name="A", slug="proj-a", created_by="user-1", user_id="user-1"
    )

    blocker = await task_store.create_task(
        project_id=prj["id"], title="Blocker", created_by="user-1"
    )
    blocked = await task_store.create_task(
        project_id=prj["id"], title="Blocked", created_by="user-1", priority=10
    )
    free = await task_store.create_task(
        project_id=prj["id"], title="Free", created_by="user-1", priority=5
    )

    await task_store.update_task(blocked["id"], labels=["claimable", f"blocked-on:{blocker['id']}"])
    await task_store.update_task(free["id"], labels=["claimable"])

    await grants_store.add_grant(
        canonical_id="agent-1", scope="project_tasks", project_id=prj["id"]
    )

    await app_state.dispatcher_store.set_config(
        "user-1",
        {
            "enabled": True,
            "boards": [prj["id"]],
            "eligible_agents": ["agent-1"],
            "max_concurrent_per_agent": 1,
        },
    )

    service = DispatcherService(app_state)
    await service.dispatch_once()

    blocked_task = await task_store.get_task(blocked["id"])
    free_task = await task_store.get_task(free["id"])
    assert blocked_task["assignee_id"] is None
    assert free_task["assignee_id"] == "agent-1"


@pytest.mark.asyncio
async def test_closing_blocker_releases_card(app_state, task_store, grants_store):
    prj = await app_state.project_store.create_project(
        name="A", slug="proj-a", created_by="user-1", user_id="user-1"
    )

    blocker = await task_store.create_task(
        project_id=prj["id"], title="Blocker", created_by="user-1"
    )
    blocked = await task_store.create_task(
        project_id=prj["id"], title="Blocked", created_by="user-1", priority=10
    )

    await task_store.update_task(blocked["id"], labels=["claimable", f"blocked-on:{blocker['id']}"])

    await grants_store.add_grant(
        canonical_id="agent-1", scope="project_tasks", project_id=prj["id"]
    )

    await app_state.dispatcher_store.set_config(
        "user-1",
        {
            "enabled": True,
            "boards": [prj["id"]],
            "eligible_agents": ["agent-1"],
            "max_concurrent_per_agent": 1,
        },
    )

    service = DispatcherService(app_state)
    await service.dispatch_once()

    blocked_task = await task_store.get_task(blocked["id"])
    assert blocked_task["assignee_id"] is None

    await task_store.close_task(blocker["id"], closed_by="user-1")

    await service.dispatch_once()
    blocked_task = await task_store.get_task(blocked["id"])
    assert blocked_task["assignee_id"] == "agent-1"


@pytest.mark.asyncio
async def test_agent_without_grant_never_gets_cards(app_state, task_store, grants_store):
    prj_a = await app_state.project_store.create_project(
        name="A", slug="proj-a", created_by="user-1", user_id="user-1"
    )
    prj_b = await app_state.project_store.create_project(
        name="B", slug="proj-b", created_by="user-1", user_id="user-1"
    )

    t_a = await task_store.create_task(
        project_id=prj_a["id"], title="A task", created_by="user-1"
    )
    t_b = await task_store.create_task(
        project_id=prj_b["id"], title="B task", created_by="user-1"
    )

    for t in [t_a, t_b]:
        await task_store.update_task(t["id"], labels=["claimable"])

    await grants_store.add_grant(
        canonical_id="agent-1", scope="project_tasks", project_id=prj_a["id"]
    )

    await app_state.dispatcher_store.set_config(
        "user-1",
        {
            "enabled": True,
            "boards": [prj_a["id"], prj_b["id"]],
            "eligible_agents": ["agent-1"],
            "max_concurrent_per_agent": 1,
        },
    )

    service = DispatcherService(app_state)
    await service.dispatch_once()

    t_a_row = await task_store.get_task(t_a["id"])
    t_b_row = await task_store.get_task(t_b["id"])
    assert t_a_row["assignee_id"] == "agent-1"
    assert t_b_row["assignee_id"] is None


@pytest.mark.asyncio
async def test_manual_claim_unaffected(app_state, task_store, grants_store):
    prj = await app_state.project_store.create_project(
        name="A", slug="proj-a", created_by="user-1", user_id="user-1"
    )

    t = await task_store.create_task(
        project_id=prj["id"], title="Claimable", created_by="user-1"
    )
    await task_store.update_task(t["id"], labels=["claimable"])

    await grants_store.add_grant(
        canonical_id="agent-1", scope="project_tasks", project_id=prj["id"]
    )

    await app_state.dispatcher_store.set_config(
        "user-1",
        {
            "enabled": True,
            "boards": [prj["id"]],
            "eligible_agents": ["agent-1"],
            "max_concurrent_per_agent": 1,
        },
    )

    service = DispatcherService(app_state)
    await service.dispatch_once()

    task = await task_store.get_task(t["id"])
    assert task["assignee_id"] == "agent-1"

    manual_ok = await task_store.claim_task(t["id"], claimer_id="manual-user")
    assert manual_ok is True

    task = await task_store.get_task(t["id"])
    assert task["claimed_by"] == "manual-user"


# ---------------------------------------------------------------------------
# Route-level integration tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path):
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(json.dumps(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    dispatcher_store = DispatcherStore(tmp_path / "dispatcher.db")
    await dispatcher_store.init()
    app.state.dispatcher_store = dispatcher_store

    app.state.auth.setup_user("admin", "Admin", "", "adminpass")
    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True

    csrf_token = secrets.token_hex(32)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    ) as c:
        yield c

    await dispatcher_store.close()


@pytest.mark.asyncio
async def test_route_get_default_config(client):
    store = client._transport.app.state.dispatcher_store
    assert store._db is not None
    resp = await client.get("/api/dispatcher/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["enabled"] is False
    assert data["config"]["max_concurrent_per_agent"] == 1
    assert data["config"]["poll_seconds"] == 30


@pytest.mark.asyncio
async def test_route_put_and_get_config(client):
    resp = await client.put(
        "/api/dispatcher/config",
        json={
            "enabled": True,
            "boards": ["board-1"],
            "eligible_agents": ["agent-x"],
            "max_concurrent_per_agent": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["enabled"] is True
    assert data["config"]["boards"] == ["board-1"]
    assert data["config"]["eligible_agents"] == ["agent-x"]
    assert data["config"]["max_concurrent_per_agent"] == 2

    resp = await client.get("/api/dispatcher/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["enabled"] is True
    assert data["config"]["boards"] == ["board-1"]
