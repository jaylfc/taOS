"""Integration test: persona_v2 migration runs on app startup."""

from __future__ import annotations

import yaml
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_legacy_agent_migrated_on_startup(tmp_path, monkeypatch):
    """A bare legacy agent (no persona fields) must be backfilled during lifespan startup."""
    # Create a minimal data dir with a config that has one legacy agent.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path = data_dir / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "server": {"host": "0.0.0.0", "port": 6969},
            "agents": [{"name": "legacy", "framework": "openclaw"}],
        })
    )

    calls: list[str] = []

    # Patch register_agent before importing create_app so the module-level
    # reference in migrations.persona_v2 picks up the mock.
    with patch("taosmd.agents.register_agent", side_effect=lambda name: calls.append(name)):
        from tinyagentos.app import create_app

        app = create_app(data_dir=data_dir)
        async with app.router.lifespan_context(app):
            agents = app.state.config.agents
            assert agents[0]["soul_md"] == ""
            assert agents[0]["memory_plugin"] == "taosmd"
            assert agents[0]["migrated_to_v2_personas"] is False
            assert agents[0]["display_name"] == "legacy"
            assert calls == ["legacy"]


@pytest.mark.asyncio
async def test_migration_is_idempotent_across_restarts(tmp_path, monkeypatch):
    """Running the lifespan twice (simulating restarts) produces identical agent state."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path = data_dir / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "server": {"host": "0.0.0.0", "port": 6969},
            "agents": [{"name": "repeatagent", "framework": "openclaw"}],
        })
    )

    from taosmd.agents import AgentExistsError

    call_count = 0

    def _register(name: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise AgentExistsError(f"{name} already registered")

    with patch("taosmd.agents.register_agent", side_effect=_register):
        from tinyagentos.app import create_app

        # First startup
        app = create_app(data_dir=data_dir)
        async with app.router.lifespan_context(app):
            agents_first = [dict(a) for a in app.state.config.agents]

        # Second startup (config has been persisted with persona fields)
        app2 = create_app(data_dir=data_dir)
        async with app2.router.lifespan_context(app2):
            agents_second = [dict(a) for a in app2.state.config.agents]

    assert agents_first[0]["soul_md"] == agents_second[0]["soul_md"]
    assert agents_first[0]["memory_plugin"] == agents_second[0]["memory_plugin"]
