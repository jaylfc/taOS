import pytest
import pytest_asyncio

from projects.conftest import seed_board_with_cards, register_agent_with_grant

from tinyagentos.agent_token_auth import active_project_grants


@pytest_asyncio.fixture
async def stores(real_stores):
    return real_stores


@pytest.mark.asyncio
async def test_real_store_fixtures_seed_and_grant(stores):
    project_row, _ = await seed_board_with_cards(stores, "u1", "Alpha", 3)
    pid = project_row["id"]
    assert len(await stores.task_store.list_ready_tasks(pid, limit=500)) == 3
    cid = await register_agent_with_grant(stores, "openclaw", pid, "project_tasks_claim")
    assert pid in await active_project_grants(stores.registry, stores.grants, cid, "project_tasks_claim")
    assert (await stores.dispatcher_store.get_config("u1")).enabled is False