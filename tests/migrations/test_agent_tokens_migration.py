import pytest
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.containers.lxc import LXCBackend
from tinyagentos.containers.docker import DockerBackend
from tinyagentos.migrations.agent_tokens_migration import run_agent_token_migration


def _store(has_token: bool = False):
    s = MagicMock()
    s.has_token = AsyncMock(return_value=has_token)
    s.issue = AsyncMock(return_value=("taos_agent_xxx", {"issued_at": "2026-05-12T00:00:00Z"}))
    return s


def _lxc_backend():
    backend = LXCBackend()
    backend.set_env = AsyncMock(return_value={"success": True, "output": ""})
    return backend


@pytest.mark.asyncio
async def test_migration_issues_tokens_for_lxc_agents_without_one():
    store = _store(has_token=False)
    backend = _lxc_backend()
    agents = [
        {"name": "agent-1", "user_id": "u1"},
        {"name": "agent-2", "user_id": "u2"},
    ]
    result = await run_agent_token_migration(
        agents=agents,
        agent_tokens_store=store,
        container_backend=backend,
    )
    assert result == {"issued": 2, "skipped_has_token": 0, "skipped_non_lxc": 0}
    assert store.issue.await_count == 2
    assert backend.set_env.await_count == 2
    assert backend.set_env.await_args_list[0].args[0] == "taos-agent-agent-1"
    assert backend.set_env.await_args_list[0].args[1] == "TAOS_TOKEN"


@pytest.mark.asyncio
async def test_migration_skips_agents_with_existing_token():
    store = _store(has_token=True)
    backend = _lxc_backend()
    agents = [{"name": "a"}]
    result = await run_agent_token_migration(
        agents=agents,
        agent_tokens_store=store,
        container_backend=backend,
    )
    assert result == {"issued": 0, "skipped_has_token": 1, "skipped_non_lxc": 0}
    store.issue.assert_not_called()
    backend.set_env.assert_not_called()


@pytest.mark.asyncio
async def test_migration_skips_everything_when_backend_is_docker():
    store = _store(has_token=False)
    backend = DockerBackend(binary="docker")
    backend.set_env = AsyncMock()
    agents = [{"name": "a"}, {"name": "b"}]
    result = await run_agent_token_migration(
        agents=agents,
        agent_tokens_store=store,
        container_backend=backend,
    )
    assert result == {"issued": 0, "skipped_has_token": 0, "skipped_non_lxc": 2}
    store.issue.assert_not_called()
    backend.set_env.assert_not_called()


@pytest.mark.asyncio
async def test_migration_skips_everything_when_backend_is_none():
    store = _store(has_token=False)
    agents = [{"name": "a"}]
    result = await run_agent_token_migration(
        agents=agents,
        agent_tokens_store=store,
        container_backend=None,
    )
    assert result == {"issued": 0, "skipped_has_token": 0, "skipped_non_lxc": 1}
    store.issue.assert_not_called()


@pytest.mark.asyncio
async def test_migration_empty_agents_is_noop():
    store = _store(has_token=False)
    backend = _lxc_backend()
    result = await run_agent_token_migration(
        agents=[],
        agent_tokens_store=store,
        container_backend=backend,
    )
    assert result == {"issued": 0, "skipped_has_token": 0, "skipped_non_lxc": 0}


@pytest.mark.asyncio
async def test_migration_revokes_issuance_when_set_env_fails():
    """If set_env fails after a successful issue, the token must be revoked so
    subsequent runs retry instead of skipping the agent (has_token would
    otherwise stay True with no TAOS_TOKEN in the container)."""
    store = _store(has_token=False)
    store.revoke_for_agent = AsyncMock(return_value=1)
    backend = LXCBackend()
    backend.set_env = AsyncMock(return_value={"success": False, "output": "incus error"})
    agents = [{"name": "agent-1"}]
    result = await run_agent_token_migration(
        agents=agents,
        agent_tokens_store=store,
        container_backend=backend,
    )
    assert result == {"issued": 0, "skipped_has_token": 0, "skipped_non_lxc": 0}
    store.issue.assert_called_once()
    backend.set_env.assert_awaited_once()
    store.revoke_for_agent.assert_awaited_once_with("agent-1")
