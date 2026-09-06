"""Tests for tinyagentos.migrations.persona_v2.migrate_agents."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest
from taosmd.agents import AgentExistsError

from tinyagentos.migrations.persona_v2 import migrate_agents


def _legacy_agent(name: str = "testagent") -> dict:
    """Minimal agent dict that predates the v2 persona fields."""
    return {"name": name, "model": "some-model"}


# ---------------------------------------------------------------------------
# test_migration_adds_persona_fields_to_legacy
# ---------------------------------------------------------------------------


def test_migration_adds_persona_fields_to_legacy():
    agents = [_legacy_agent("alpha")]
    register_fn = MagicMock()

    migrate_agents(agents, register_fn)

    agent = agents[0]
    assert agent["soul_md"] == ""
    assert agent["agent_md"] == ""
    assert agent["memory_plugin"] == "taosmd"
    assert agent["source_persona_id"] is None
    assert agent["migrated_to_v2_personas"] is False
    assert agent["display_name"] == "alpha"


# ---------------------------------------------------------------------------
# test_migration_is_idempotent
# ---------------------------------------------------------------------------


def test_migration_is_idempotent():
    agents = [_legacy_agent("beta")]
    register_fn = MagicMock()

    migrate_agents(agents, register_fn)
    snapshot_after_first = dict(agents[0])

    # Second run — register_fn raises AgentExistsError (already registered).
    register_fn.side_effect = AgentExistsError("beta already registered")
    migrate_agents(agents, register_fn)
    snapshot_after_second = dict(agents[0])

    assert snapshot_after_first == snapshot_after_second


# ---------------------------------------------------------------------------
# test_migration_calls_register_once_per_agent
# ---------------------------------------------------------------------------


def test_migration_calls_register_once_per_agent():
    agents = [
        _legacy_agent("gamma"),
        _legacy_agent("delta"),
        _legacy_agent("epsilon"),
    ]
    register_fn = MagicMock()

    migrate_agents(agents, register_fn)

    assert register_fn.call_count == 3
    register_fn.assert_has_calls(
        [call("gamma"), call("delta"), call("epsilon")], any_order=False
    )


# ---------------------------------------------------------------------------
# test_migration_handles_register_already_exists
# ---------------------------------------------------------------------------


def test_migration_handles_register_already_exists():
    agents = [_legacy_agent("zeta")]
    register_fn = MagicMock(side_effect=AgentExistsError("zeta already registered"))

    # Must not raise; fields must still be set.
    result = migrate_agents(agents, register_fn)

    agent = result[0]
    assert agent["soul_md"] == ""
    assert agent["display_name"] == "zeta"
    register_fn.assert_called_once_with("zeta")


# ---------------------------------------------------------------------------
# Existing non-default values are preserved (not clobbered)
# ---------------------------------------------------------------------------


def test_migration_preserves_existing_values():
    agents = [
        {
            "name": "eta",
            "soul_md": "custom soul",
            "agent_md": "custom agent",
            "memory_plugin": "custom_plugin",
            "source_persona_id": "abc123",
            "migrated_to_v2_personas": True,
            "display_name": "Eta Display",
        }
    ]
    register_fn = MagicMock()

    migrate_agents(agents, register_fn)

    agent = agents[0]
    assert agent["soul_md"] == "custom soul"
    assert agent["agent_md"] == "custom agent"
    assert agent["memory_plugin"] == "custom_plugin"
    assert agent["source_persona_id"] == "abc123"
    assert agent["migrated_to_v2_personas"] is True
    assert agent["display_name"] == "Eta Display"
