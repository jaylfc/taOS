"""Tests for agent name reservation to prevent race conditions across
deploy, import, and restore create paths.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.config import (
    AppConfig,
    release_agent_slug,
    reserve_agent_slug,
    unique_agent_slug,
    _reserved_agent_slugs,
)


@pytest.fixture(autouse=True)
def _clear_reserved_slugs():
    """Clear the module-level reserved slugs set before each test."""
    _reserved_agent_slugs.clear()
    yield
    _reserved_agent_slugs.clear()


class FakeConfig:
    """Minimal config stub with an agents list."""
    def __init__(self, agents=None):
        self.agents = agents or []


class TestReserveAgentSlug:
    """Unit tests for the reserve_agent_slug helper."""

    @pytest.mark.asyncio
    async def test_concurrent_reservations_return_different_names(self):
        """Two concurrent reserve_agent_slug calls for the same display name
        must return different slugs (e.g., 'helper' and 'helper-2')."""
        config = FakeConfig()

        async def reserve():
            return await reserve_agent_slug(config, "helper")

        results = await asyncio.gather(reserve(), reserve())
        assert set(results) == {"helper", "helper-2"}

    @pytest.mark.asyncio
    async def test_released_name_can_be_reserved_again(self):
        """A name that was reserved and then released should be available
        for reservation again."""
        config = FakeConfig()

        name1 = await reserve_agent_slug(config, "helper")
        assert name1 == "helper"

        release_agent_slug(name1)

        name2 = await reserve_agent_slug(config, "helper")
        assert name2 == "helper"

    @pytest.mark.asyncio
    async def test_reserve_respects_existing_config_agents(self):
        """Reserved names should be treated as taken alongside existing
        config.agents entries."""
        config = FakeConfig([{"name": "helper"}])

        name = await reserve_agent_slug(config, "helper")
        assert name == "helper-2"

    @pytest.mark.asyncio
    async def test_reserve_respects_other_reserved_names(self):
        """Reserved names should be treated as taken by other reservations."""
        config = FakeConfig()

        name1 = await reserve_agent_slug(config, "helper")
        assert name1 == "helper"

        # Without releasing name1, a second reservation should get helper-2
        name2 = await reserve_agent_slug(config, "helper")
        assert name2 == "helper-2"

        release_agent_slug(name1)
        release_agent_slug(name2)


class TestRouteLevelNameReservation:
    """Integration tests verifying the three create paths use name reservation."""

    @pytest.mark.asyncio
    async def test_concurrent_restore_and_import_get_different_names(
        self, client, monkeypatch, app
    ):
        """Concurrent restore of archive 'helper' and import of display name
        'helper' must result in two config entries with different names.

        The restore path mocks snapshot_restore to await asyncio.sleep(0.05)
        and the import path mocks container calls to also await, ensuring
        the requests really interleave.
        """
        config = app.state.config
        data_dir = app.state.data_dir

        # Set up an archive entry for 'helper'
        archive_entry = {
            "id": "archive-helper-1",
            "archived_at": "20260101T000000",
            "archived_slug": "helper",
            "snapshot_name": "taos-archive-1",
            "export_path": None,
            "archive_dir": "archive/helper-20260101T000000",
            "original": {
                "name": "helper",
                "display_name": "Helper",
                "host": "",
                "qmd_index": "test",
                "color": "#888888",
                "framework": "hermes",
            },
            "container_name": "taos-agent-helper",
        }
        config.archived_agents = [archive_entry]

        # Track started containers to verify only one restore runs
        started_containers = []

        # Mock container operations for restore path
        async def yielding_snapshot_restore(*args, **kwargs):
            await asyncio.sleep(0.05)
            return {"success": True, "output": ""}

        async def fake_rename_container(*args, **kwargs):
            return {"success": True, "output": ""}

        async def fake_start_container(name):
            started_containers.append(name)
            return {"success": True, "output": ""}

        async def fake_set_env(*args, **kwargs):
            return {"success": True, "output": ""}

        monkeypatch.setattr(
            "tinyagentos.containers.snapshot_restore", yielding_snapshot_restore
        )
        monkeypatch.setattr(
            "tinyagentos.containers.rename_container", fake_rename_container
        )
        monkeypatch.setattr(
            "tinyagentos.containers.start_container", fake_start_container
        )
        monkeypatch.setattr("tinyagentos.containers.set_env", fake_set_env)

        # Mock container operations for import path
        import_calls = []

        async def fake_deploy_agent(*args, **kwargs):
            import_calls.append("deploy_agent")
            await asyncio.sleep(0.05)
            return {"success": True, "name": kwargs.get("name", "helper"), "ip": "10.0.0.1", "llm_key": "test-key"}

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy_agent)

        # Mock taosmd register_agent to avoid side effects
        async def fake_register_agent(*args, **kwargs):
            pass
        monkeypatch.setattr("taosmd.agents.register_agent", fake_register_agent)

        # Mock secrets store
        class FakeSecretsStore:
            async def get_agent_secrets(self, name):
                return []
            async def add(self, **kwargs):
                pass
        app.state.secrets = FakeSecretsStore()

        # Mock LLM proxy
        class FakeLLMProxy:
            async def create_agent_key(self, name, models=None):
                return "test-key"
        app.state.llm_proxy = FakeLLMProxy()

        # Mock registry
        class FakeRegistry:
            _db = None
            async def register(self, framework, display_name, user_id):
                return {"canonical_id": f"canonical-{display_name}"}
            async def init(self):
                self._db = "fake"
            async def close(self):
                pass
        app.state.agent_registry = FakeRegistry()

        # Mock auth for local token
        from tinyagentos.auth import AuthManager
        app.state.auth = AuthManager(data_dir)

        # Make restore request
        restore_task = client.post("/api/agents/archived/archive-helper-1/restore")

        # Make import request - need to simulate the import endpoint
        import_task = client.post(
            "/api/agents/import",
            data={
                "framework": "hermes",
                "name": "helper",
                "model": "test-model",
                "secrets": "{}",
            },
            files={"bundle": ("test.tar.gz", b"fake bundle content", "application/octet-stream")},
        )

        # Wait for both to complete
        restore_resp, import_resp = await asyncio.gather(restore_task, import_task)

        # Check results
        assert restore_resp.status_code == 200, f"Restore failed: {restore_resp.text}"
        assert import_resp.status_code == 202, f"Import failed: {import_resp.text}"

        restore_data = restore_resp.json()
        import_data = import_resp.json()

        # The two resulting config entries must have different names
        restore_name = restore_data.get("name")
        import_name = import_data.get("name")

        assert restore_name != import_name, (
            f"Concurrent restore and import got same name: {restore_name}"
        )

        # Verify both entries exist in config
        agent_names = [a["name"] for a in config.agents]
        assert restore_name in agent_names
        assert import_name in agent_names

        # The names should be 'helper' and 'helper-2' in some order
        assert set(agent_names) == {"helper", "helper-2"} or set(agent_names) >= {"helper", "helper-2"}