"""Tests for AgentRef identity resolver (projects/agent_ref.py)."""
from __future__ import annotations

import pytest
import pytest_asyncio
from dataclasses import is_dataclass

from tinyagentos.projects.agent_ref import AgentRef, resolve_agent_refs


class _FakeRegistry:
    def __init__(self, records: dict[str, dict]):
        self._records = records

    async def get(self, canonical_id: str):
        return self._records.get(canonical_id)


class _FakeConfig:
    def __init__(self, agents: list[dict]):
        self.agents = agents


class TestAgentRefDataclass:
    """Tests for the AgentRef dataclass structure and methods."""

    def test_is_frozen_dataclass(self):
        assert is_dataclass(AgentRef)
        assert AgentRef.__dataclass_params__.frozen is True

    def test_fields_exist(self):
        ref = AgentRef(
            canonical_id="cid-1",
            config_id="agent-hex-123",
            name="Test Agent",
            deployed=True,
            running=True,
        )
        assert ref.canonical_id == "cid-1"
        assert ref.config_id == "agent-hex-123"
        assert ref.name == "Test Agent"
        assert ref.deployed is True
        assert ref.running is True

    def test_aliases_includes_canonical_and_config_id(self):
        ref = AgentRef(
            canonical_id="cid-1",
            config_id="agent-hex-123",
            name="Test Agent",
            deployed=True,
            running=True,
        )
        assert ref.aliases() == ("cid-1", "agent-hex-123")

    def test_aliases_only_canonical_when_no_config_id(self):
        ref = AgentRef(
            canonical_id="cid-1",
            config_id=None,
            name="External Agent",
            deployed=False,
            running=False,
        )
        assert ref.aliases() == ("cid-1",)

    def test_assignee_value_uses_config_id_when_deployed(self):
        ref = AgentRef(
            canonical_id="cid-1",
            config_id="agent-hex-123",
            name="Test Agent",
            deployed=True,
            running=True,
        )
        assert ref.assignee_value() == "agent-hex-123"

    def test_assignee_value_uses_canonical_id_when_not_deployed(self):
        ref = AgentRef(
            canonical_id="cid-1",
            config_id=None,
            name="External Agent",
            deployed=False,
            running=False,
        )
        assert ref.assignee_value() == "cid-1"

    def test_assignee_value_uses_canonical_id_when_deployed_but_no_config_id(self):
        """Edge case: deployed=True but config_id is None (should not happen normally)."""
        ref = AgentRef(
            canonical_id="cid-1",
            config_id=None,
            name="Weird Agent",
            deployed=True,
            running=False,
        )
        assert ref.assignee_value() == "cid-1"


@pytest.mark.asyncio
class TestResolveAgentRefs:
    """Tests for resolve_agent_refs function."""

    @pytest_asyncio.fixture
    async def setup(self):
        registry = _FakeRegistry({
            "cid-deployed": {"canonical_id": "cid-deployed", "status": "active", "display_name": "Deployed Agent"},
            "cid-external": {"canonical_id": "cid-external", "status": "active", "display_name": "External Agent"},
            "cid-revoked": {"canonical_id": "cid-revoked", "status": "revoked", "display_name": "Revoked Agent"},
            "cid-suspended": {"canonical_id": "cid-suspended", "status": "suspended", "display_name": "Suspended Agent"},
            "cid-owned-by-other": {"canonical_id": "cid-owned-by-other", "status": "active", "display_name": "Other User Agent", "user_id": "other-user"},
        })
        config = _FakeConfig([
            {"id": "deployed-hex-123", "name": "Deployed Agent", "registry_canonical_id": "cid-deployed"},
            {"id": "revoked-hex-789", "name": "Revoked Agent", "registry_canonical_id": "cid-revoked"},
            {"id": "other-hex-abc", "name": "Other User Agent", "registry_canonical_id": "cid-owned-by-other"},
        ])
        return registry, config

    async def test_resolve_agent_refs_deployed_uses_config_id_as_assignee(self, setup):
        registry, config = setup
        refs = await resolve_agent_refs(["cid-deployed"], config, registry)
        assert len(refs) == 1
        ref = refs[0]
        assert ref.canonical_id == "cid-deployed"
        assert ref.config_id == "deployed-hex-123"
        assert ref.name == "Deployed Agent"
        assert ref.deployed is True
        assert ref.running is False  # running is not determined here
        assert ref.assignee_value() == "deployed-hex-123"

    async def test_resolve_agent_refs_external_uses_canonical_id(self, setup):
        registry, config = setup
        refs = await resolve_agent_refs(["cid-external"], config, registry)
        assert len(refs) == 1
        ref = refs[0]
        assert ref.canonical_id == "cid-external"
        assert ref.config_id is None
        assert ref.name == "External Agent"
        assert ref.deployed is False  # external agents are not deployed
        assert ref.assignee_value() == "cid-external"

    async def test_resolve_agent_refs_drops_revoked_identity(self, setup):
        registry, config = setup
        refs = await resolve_agent_refs(["cid-revoked"], config, registry)
        assert refs == []

    async def test_resolve_agent_refs_drops_suspended_identity(self, setup):
        registry, config = setup
        refs = await resolve_agent_refs(["cid-suspended"], config, registry)
        assert refs == []

    async def test_resolve_agent_refs_drops_missing_registry_record(self, setup):
        registry, config = setup
        refs = await resolve_agent_refs(["cid-missing"], config, registry)
        assert refs == []

    async def test_resolve_agent_refs_keeps_agent_owned_by_another_user(self, setup):
        """NO owner/user_id filter - grants are checked separately."""
        registry, config = setup
        refs = await resolve_agent_refs(["cid-owned-by-other"], config, registry)
        assert len(refs) == 1
        ref = refs[0]
        assert ref.canonical_id == "cid-owned-by-other"
        assert ref.name == "Other User Agent"
        assert ref.assignee_value() == "other-hex-abc"

    async def test_resolve_agent_refs_multiple_ids(self, setup):
        registry, config = setup
        refs = await resolve_agent_refs(
            ["cid-deployed", "cid-external", "cid-revoked", "cid-missing"],
            config,
            registry
        )
        assert len(refs) == 2
        canonical_ids = {r.canonical_id for r in refs}
        assert canonical_ids == {"cid-deployed", "cid-external"}

    async def test_resolve_agent_refs_empty_input(self, setup):
        registry, config = setup
        refs = await resolve_agent_refs([], config, registry)
        assert refs == []

    async def test_resolve_agent_refs_config_id_none_when_no_match(self, setup):
        """When canonical_id not found in config.agents, config_id is None."""
        registry = _FakeRegistry({
            "cid-no-config": {"canonical_id": "cid-no-config", "status": "active", "display_name": "No Config Agent"},
        })
        config = _FakeConfig([])
        refs = await resolve_agent_refs(["cid-no-config"], config, registry)
        assert len(refs) == 1
        ref = refs[0]
        assert ref.canonical_id == "cid-no-config"
        assert ref.config_id is None
        assert ref.name == "No Config Agent"
        assert ref.deployed is False
        assert ref.assignee_value() == "cid-no-config"