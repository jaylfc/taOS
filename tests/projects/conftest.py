import pytest_asyncio
from dataclasses import dataclass, field

from tinyagentos.projects.dispatcher_store import DispatcherStore
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import AgentRegistryStore


@pytest_asyncio.fixture
async def real_project_store(tmp_path):
    s = ProjectStore(tmp_path / "projects.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def real_task_store(tmp_path, real_project_store):
    s = ProjectTaskStore(tmp_path / "projects.db", project_store=real_project_store)
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def real_dispatcher_store(tmp_path):
    s = DispatcherStore(tmp_path / "projects.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def real_grants_store(tmp_path):
    s = AgentGrantsStore(tmp_path / "grants.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def real_registry_store(tmp_path):
    s = AgentRegistryStore(tmp_path / "agent_registry.db")
    await s.init()
    yield s
    await s.close()


@dataclass
class _RealStores:
    project_store: ProjectStore
    task_store: ProjectTaskStore
    dispatcher_store: DispatcherStore
    grants: AgentGrantsStore
    registry: AgentRegistryStore


@pytest_asyncio.fixture
async def real_stores(real_project_store, real_task_store, real_dispatcher_store, real_grants_store, real_registry_store):
    return _RealStores(
        project_store=real_project_store,
        task_store=real_task_store,
        dispatcher_store=real_dispatcher_store,
        grants=real_grants_store,
        registry=real_registry_store,
    )


async def seed_board_with_cards(stores, user_id, name, n):
    project_row = await stores.project_store.create_project(
        name=name,
        slug=name.lower().replace(" ", "-"),
        created_by=user_id,
        description="",
        user_id=user_id,
    )
    pid = project_row["id"]
    tasks = []
    for i in range(n):
        task_row = await stores.task_store.create_task(
            project_id=pid,
            title=f"{name} task {i}",
            created_by=user_id,
            body="",
        )
        tasks.append(task_row)
    return project_row, tasks


async def register_agent_with_grant(stores, framework, project_id, scope):
    reg_result = await stores.registry.register(
        framework=framework,
        display_name=framework,
    )
    canonical_id = reg_result["canonical_id"]
    await stores.grants.add_grant(
        canonical_id=canonical_id,
        scope=scope,
        project_id=project_id,
    )
    return canonical_id