from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import AgentRegistryStore
from tinyagentos.projects.dispatcher import DispatcherService, DispatcherStore
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.task_store import ProjectTaskStore


@pytest.fixture
def dispatcher_db(tmp_path):
    store = DispatcherStore(tmp_path / "dispatcher.db")
    return store


@pytest.mark.asyncio
async def test_dispatcher_store_defaults(dispatcher_db):
    await dispatcher_db.init()
    cfg = await dispatcher_db.get_config("user-1")
    assert cfg["enabled"] is False
    assert cfg["boards"] == []
    assert cfg["eligible_agents"] == []
    assert cfg["max_concurrent_per_agent"] == 1
    assert cfg["poll_seconds"] == 30
    await dispatcher_db.close()


@pytest.mark.asyncio
async def test_dispatcher_store_crud(dispatcher_db):
    await dispatcher_db.init()
    cfg = await dispatcher_db.update_config(
        "user-1",
        enabled=True,
        boards=["prj-1", "prj-2"],
        eligible_agents=["agent-a", "agent-b"],
        max_concurrent_per_agent=2,
        poll_seconds=60,
    )
    assert cfg["enabled"] is True
    assert cfg["boards"] == ["prj-1", "prj-2"]
    assert cfg["eligible_agents"] == ["agent-a", "agent-b"]
    assert cfg["max_concurrent_per_agent"] == 2
    assert cfg["poll_seconds"] == 60

    # Re-read
    cfg2 = await dispatcher_db.get_config("user-1")
    assert cfg2 == cfg

    # Partial update
    cfg3 = await dispatcher_db.update_config("user-1", enabled=False)
    assert cfg3["enabled"] is False
    assert cfg3["boards"] == ["prj-1", "prj-2"]
    await dispatcher_db.close()


@pytest.mark.asyncio
async def test_dispatcher_disabled_by_default(tmp_path):
    store = DispatcherStore(tmp_path / "dispatcher.db")
    await store.init()
    cfg = await store.get_config("user-1")
    assert cfg["enabled"] is False
    await store.close()


def _make_app_state(tmp_path, agents, registry_rows, grant_rows, projects, tasks):
    class FakeConfig:
        pass
    FakeConfig.agents = agents

    class FakeTaskStore:
        def __init__(self, tasks):
            self._tasks = {t["id"]: t for t in tasks}

        async def get_task(self, task_id):
            return self._tasks.get(task_id)

        @asynccontextmanager
        async def _read(self, sql, params=()):
            class FakeCursor:
                def __init__(self, rows, desc):
                    self._rows = rows
                    self._desc = desc

                async def fetchone(self):
                    return self._rows[0] if self._rows else None

                async def fetchall(self):
                    return self._rows

                @property
                def description(self):
                    return self._desc

            # Parse SQL to determine what to return
            sql_lower = sql.lower()
            if "count(*)" in sql_lower and "claimed_by" in sql_lower:
                agent_id = params[0]
                count = sum(1 for t in self._tasks.values() if t.get("claimed_by") == agent_id and t.get("status") == "claimed")
                yield FakeCursor([(count,)], [("count", None, None, None, None, None, None)])
                return
            if "from project_tasks" in sql_lower:
                filtered = list(self._tasks.values())
                if params:
                    board_ids = set(params[:-1]) if isinstance(params, (list, tuple)) and len(params) > 1 else set()
                    if board_ids:
                        filtered = [t for t in filtered if t.get("project_id") in board_ids]
                filtered = [t for t in filtered if t.get("status") == "open"]
                filtered = [t for t in filtered if t.get("claimed_by") is None]
                filtered = [t for t in filtered if any(l in ("claimable", "fleet:claimable") for l in (json.loads(t.get("labels") or "[]") if isinstance(t.get("labels"), str) else (t.get("labels") or [])))]
                result = []
                for t in filtered:
                    blocked = False
                    raw_labels = t.get("labels") or "[]"
                    labels = json.loads(raw_labels) if isinstance(raw_labels, str) else raw_labels
                    for lbl in labels:
                        if lbl.startswith("blocked-on:"):
                            dep_id = lbl[len("blocked-on:"):]
                            dep = self._tasks.get(dep_id)
                            if dep and dep.get("status") not in ("closed", "cancelled"):
                                blocked = True
                                break
                    if not blocked:
                        result.append(t)
                result.sort(key=lambda t: (-(t.get("priority") or 0), t.get("created_at") or 0))
                limit = params[-1] if params and isinstance(params[-1], int) else None
                if limit is not None:
                    result = result[:limit]
                rows = []
                desc = [
                    ("id", None, None, None, None, None, None),
                    ("project_id", None, None, None, None, None, None),
                    ("parent_task_id", None, None, None, None, None, None),
                    ("title", None, None, None, None, None, None),
                    ("body", None, None, None, None, None, None),
                    ("status", None, None, None, None, None, None),
                    ("priority", None, None, None, None, None, None),
                    ("labels", None, None, None, None, None, None),
                    ("assignee_id", None, None, None, None, None, None),
                    ("element_id", None, None, None, None, None, None),
                    ("claimed_by", None, None, None, None, None, None),
                    ("claimed_at", None, None, None, None, None, None),
                    ("closed_at", None, None, None, None, None, None),
                    ("closed_by", None, None, None, None, None, None),
                    ("close_reason", None, None, None, None, None, None),
                    ("created_by", None, None, None, None, None, None),
                    ("created_at", None, None, None, None, None, None),
                    ("updated_at", None, None, None, None, None, None),
                ]
                for t in result:
                    rows.append(tuple(t.get(k) for k, *_ in desc))
                yield FakeCursor(rows, desc)
                return
            yield FakeCursor([], [])

        async def update_task(self, task_id, **kwargs):
            t = self._tasks.get(task_id)
            if t:
                t.update(kwargs)

    class FakeProjectStore:
        def __init__(self, projects):
            self._projects = {p["id"]: p for p in projects}

        async def get_project(self, project_id):
            return self._projects.get(project_id)

        async def list_for_user(self, user_id, status="active"):
            return [p for p in self._projects.values() if p.get("user_id") == user_id and p.get("status") == (status or "active")]

    class FakeGrantsStore:
        def __init__(self, grants):
            self._grants = grants

        async def list_grants(self, canonical_id):
            return [g for g in self._grants if g.get("canonical_id") == canonical_id]

    class FakeRegistryStore:
        def __init__(self, rows):
            self._rows = {r["canonical_id"]: r for r in rows}

        async def get(self, canonical_id):
            return self._rows.get(canonical_id)

        async def get_by_slug(self, slug, status="active"):
            for r in self._rows.values():
                if status is not None and r.get("status") != status:
                    continue
                # canonical_id = {slug}-{date}[-{hex}]
                cid = r.get("canonical_id", "")
                if cid.startswith(slug + "-"):
                    return r
            return None

    task_store = FakeTaskStore(tasks)
    project_store = FakeProjectStore(projects)
    grants_store = FakeGrantsStore(grant_rows)
    registry_store = FakeRegistryStore(registry_rows)

    class FakeAppState:
        pass
    FakeAppState.config = FakeConfig()
    FakeAppState.project_task_store = task_store
    FakeAppState.project_store = project_store
    FakeAppState.agent_grants = grants_store
    FakeAppState.agent_registry = registry_store
    FakeAppState.data_dir = tmp_path

    return FakeAppState()


@pytest.mark.asyncio
async def test_dispatcher_respects_per_agent_cap(tmp_path):

    projects = [
        {"id": "prj-1", "name": "P1", "slug": "p1", "user_id": "user-1", "status": "active"},
        {"id": "prj-2", "name": "P2", "slug": "p2", "user_id": "user-1", "status": "active"},
    ]
    now = time.time()
    tasks = [
        {"id": "tsk-1", "project_id": "prj-1", "title": "High priority", "status": "open", "priority": 10, "labels": json.dumps(["claimable"]), "assignee_id": None, "claimed_by": None, "created_at": now - 10, "updated_at": now - 10},
        {"id": "tsk-2", "project_id": "prj-2", "title": "Medium priority", "status": "open", "priority": 5, "labels": json.dumps(["claimable"]), "assignee_id": None, "claimed_by": None, "created_at": now - 5, "updated_at": now - 5},
        {"id": "tsk-3", "project_id": "prj-1", "title": "Low priority", "status": "open", "priority": 1, "labels": json.dumps(["claimable"]), "assignee_id": None, "claimed_by": None, "created_at": now, "updated_at": now},
    ]
    # agent-alpha already claimed tsk-2 (cap = 1)
    tasks[1]["claimed_by"] = "canon-alpha"
    tasks[1]["status"] = "claimed"

    agents_config = [
        {"id": "cfg-alpha", "name": "alpha", "user_id": "user-1"},
        {"id": "cfg-beta", "name": "beta", "user_id": "user-1"},
    ]
    registry_rows = [
        {"canonical_id": "canon-alpha", "display_name": "Alpha Agent", "status": "active"},
        {"canonical_id": "canon-beta", "display_name": "Beta Agent", "status": "active"},
    ]
    grant_rows = [
        {"canonical_id": "canon-alpha", "scope": "project_tasks", "project_id": "prj-1", "expires_at": None},
        {"canonical_id": "canon-alpha", "scope": "project_tasks", "project_id": "prj-2", "expires_at": None},
        {"canonical_id": "canon-beta", "scope": "project_tasks", "project_id": "prj-1", "expires_at": None},
        {"canonical_id": "canon-beta", "scope": "project_tasks", "project_id": "prj-2", "expires_at": None},
    ]

    app_state = _make_app_state(tmp_path, agents_config, registry_rows, grant_rows, projects, tasks)
    dispatcher_db = DispatcherStore(tmp_path / "dispatcher.db")
    await dispatcher_db.init()
    await dispatcher_db.update_config(
        "user-1",
        enabled=True,
        boards=[],
        eligible_agents=["canon-alpha", "canon-beta"],
        max_concurrent_per_agent=1,
    )

    service = DispatcherService(
        app_state=app_state,
        dispatcher_store=dispatcher_db,
        project_task_store=app_state.project_task_store,
        agent_grants_store=app_state.agent_grants,
        agent_registry_store=app_state.agent_registry,
        project_store=app_state.project_store,
    )

    with patch("tinyagentos.agent_heartbeat._wake_agent_with_task", new_callable=AsyncMock, return_value=True):
        await service.dispatch_cycle("user-1")

    # alpha is at cap (1 claimed), so only beta gets the highest-priority unclaimed task (tsk-1)
    assert tasks[0]["assignee_id"] == "canon-beta"
    # tsk-2 remains claimed by alpha (unchanged)
    assert tasks[1]["claimed_by"] == "canon-alpha"
    # tsk-3 remains unassigned (beta already used)
    assert tasks[2].get("assignee_id") is None

    await dispatcher_db.close()


@pytest.mark.asyncio
async def test_dispatcher_blocks_on_dependency(tmp_path):
    projects = [
        {"id": "prj-1", "name": "P1", "slug": "p1", "user_id": "user-1", "status": "active"},
    ]
    now = time.time()
    blocker = {"id": "tsk-blocker", "project_id": "prj-1", "title": "Blocker", "status": "open", "priority": 0, "labels": json.dumps([]), "assignee_id": None, "claimed_by": None, "created_at": now - 20, "updated_at": now - 20}
    blocked_task = {"id": "tsk-blocked", "project_id": "prj-1", "title": "Blocked", "status": "open", "priority": 10, "labels": json.dumps(["claimable", "blocked-on:tsk-blocker"]), "assignee_id": None, "claimed_by": None, "created_at": now - 10, "updated_at": now - 10}
    tasks = [blocker, blocked_task]

    agents_config = [{"id": "cfg-1", "name": "agent1", "user_id": "user-1"}]
    registry_rows = [{"canonical_id": "canon-1", "display_name": "Agent One", "status": "active"}]
    grant_rows = [{"canonical_id": "canon-1", "scope": "project_tasks", "project_id": "prj-1", "expires_at": None}]

    app_state = _make_app_state(tmp_path, agents_config, registry_rows, grant_rows, projects, tasks)
    dispatcher_db = DispatcherStore(tmp_path / "dispatcher.db")
    await dispatcher_db.init()
    await dispatcher_db.update_config(
        "user-1",
        enabled=True,
        boards=["prj-1"],
        eligible_agents=["canon-1"],
        max_concurrent_per_agent=1,
    )

    service = DispatcherService(
        app_state=app_state,
        dispatcher_store=dispatcher_db,
        project_task_store=app_state.project_task_store,
        agent_grants_store=app_state.agent_grants,
        agent_registry_store=app_state.agent_registry,
        project_store=app_state.project_store,
    )

    candidates = await service._get_candidates_for_user("user-1")
    assert len(candidates) == 0

    # Closing the blocker releases the card
    blocker["status"] = "closed"
    candidates = await service._get_candidates_for_user("user-1")
    assert len(candidates) == 1
    assert candidates[0][0]["id"] == "tsk-blocked"

    await dispatcher_db.close()


@pytest.mark.asyncio
async def test_dispatcher_agent_without_grant_is_skipped(tmp_path):
    projects = [{"id": "prj-1", "name": "P1", "slug": "p1", "user_id": "user-1", "status": "active"}]
    now = time.time()
    tasks = [
        {"id": "tsk-1", "project_id": "prj-1", "title": "Task", "status": "open", "priority": 10, "labels": json.dumps(["claimable"]), "assignee_id": None, "claimed_by": None, "created_at": now, "updated_at": now},
    ]
    agents_config = [{"id": "cfg-1", "name": "agent1", "user_id": "user-1"}]
    registry_rows = [{"canonical_id": "canon-1", "display_name": "Agent One", "status": "active"}]
    # No grant for canon-1 on prj-1
    grant_rows = []

    app_state = _make_app_state(tmp_path, agents_config, registry_rows, grant_rows, projects, tasks)
    dispatcher_db = DispatcherStore(tmp_path / "dispatcher.db")
    await dispatcher_db.init()
    await dispatcher_db.update_config(
        "user-1",
        enabled=True,
        boards=["prj-1"],
        eligible_agents=["canon-1"],
        max_concurrent_per_agent=1,
    )

    service = DispatcherService(
        app_state=app_state,
        dispatcher_store=dispatcher_db,
        project_task_store=app_state.project_task_store,
        agent_grants_store=app_state.agent_grants,
        agent_registry_store=app_state.agent_registry,
        project_store=app_state.project_store,
    )

    candidates = await service._get_candidates_for_user("user-1")
    assert len(candidates) == 0
    await dispatcher_db.close()


@pytest.mark.asyncio
async def test_dispatcher_pools_across_boards(tmp_path):
    projects = [
        {"id": "prj-1", "name": "P1", "slug": "p1", "user_id": "user-1", "status": "active"},
        {"id": "prj-2", "name": "P2", "slug": "p2", "user_id": "user-1", "status": "active"},
    ]
    now = time.time()
    tasks = [
        {"id": "tsk-1", "project_id": "prj-1", "title": "High on P2", "status": "open", "priority": 10, "labels": json.dumps(["claimable"]), "assignee_id": None, "claimed_by": None, "created_at": now - 5, "updated_at": now - 5},
        {"id": "tsk-2", "project_id": "prj-2", "title": "Low on P1", "status": "open", "priority": 1, "labels": json.dumps(["claimable"]), "assignee_id": None, "claimed_by": None, "created_at": now, "updated_at": now},
    ]
    agents_config = [{"id": "cfg-1", "name": "agent1", "user_id": "user-1"}]
    registry_rows = [{"canonical_id": "canon-1", "display_name": "Agent One", "status": "active"}]
    grant_rows = [
        {"canonical_id": "canon-1", "scope": "project_tasks", "project_id": "prj-1", "expires_at": None},
        {"canonical_id": "canon-1", "scope": "project_tasks", "project_id": "prj-2", "expires_at": None},
    ]

    app_state = _make_app_state(tmp_path, agents_config, registry_rows, grant_rows, projects, tasks)
    dispatcher_db = DispatcherStore(tmp_path / "dispatcher.db")
    await dispatcher_db.init()
    await dispatcher_db.update_config(
        "user-1",
        enabled=True,
        boards=[],
        eligible_agents=["canon-1"],
        max_concurrent_per_agent=1,
    )

    service = DispatcherService(
        app_state=app_state,
        dispatcher_store=dispatcher_db,
        project_task_store=app_state.project_task_store,
        agent_grants_store=app_state.agent_grants,
        agent_registry_store=app_state.agent_registry,
        project_store=app_state.project_store,
    )

    with patch("tinyagentos.agent_heartbeat._wake_agent_with_task", new_callable=AsyncMock, return_value=True):
        await service.dispatch_cycle("user-1")

    # Highest priority first: tsk-1 (priority 10) even though it's on prj-2
    assert tasks[0]["assignee_id"] == "canon-1"
    assert tasks[1]["assignee_id"] is None
    await dispatcher_db.close()


@pytest.mark.asyncio
async def test_dispatcher_manual_claim_unaffected(tmp_path):
    from tinyagentos.projects.task_store import ProjectTaskStore

    db_path = tmp_path / "projects.db"
    task_store = ProjectTaskStore(db_path)
    await task_store.init()

    t = await task_store.create_task(project_id="prj-1", title="Manual", created_by="u", labels=["claimable"])
    ok = await task_store.claim_task(t["id"], claimer_id="manual-agent")
    assert ok is True

    dispatcher_db = DispatcherStore(tmp_path / "dispatcher.db")
    await dispatcher_db.init()
    await dispatcher_db.update_config("user-1", enabled=True, boards=["prj-1"], eligible_agents=["canon-1"])

    # Re-read task after manual claim
    task = await task_store.get_task(t["id"])
    assert task["claimed_by"] == "manual-agent"
    assert task["status"] == "claimed"

    await task_store.close()
    await dispatcher_db.close()
