import pytest
from pathlib import Path

from tinyagentos.app import create_app


class TestAppAgentRegistry:
    def test_create_app_roots_registry_at_data_dir(self, tmp_path):
        app_a = create_app(data_dir=tmp_path / "a")
        app_b = create_app(data_dir=tmp_path / "b")

        assert app_a.state.taosmd_agent_registry is not None
        assert app_b.state.taosmd_agent_registry is not None
        assert app_a.state.taosmd_agent_registry is not app_b.state.taosmd_agent_registry
        assert app_a.state.taosmd_agent_registry.registry_path == tmp_path / "a" / "agents.json"
        assert app_b.state.taosmd_agent_registry.registry_path == tmp_path / "b" / "agents.json"

    def test_app_scoped_registry_is_same_object_as_global(self, tmp_path):
        app = create_app(data_dir=tmp_path / "single")
        import taosmd.agents as tm_agents
        assert app.state.taosmd_agent_registry is tm_agents._default_registry

    def test_deploy_route_registers_into_app_scoped_registry(self, tmp_path):
        import taosmd.agents as tm_agents

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        app_a = create_app(data_dir=dir_a)
        app_b = create_app(data_dir=dir_b)

        names_a = {r["name"] for r in app_a.state.taosmd_agent_registry.list_agents()}
        names_b = {r["name"] for r in app_b.state.taosmd_agent_registry.list_agents()}
        assert names_a == set()
        assert names_b == set()

        app_a.state.taosmd_agent_registry.register_agent("agent-a-only")
        names_a = {r["name"] for r in app_a.state.taosmd_agent_registry.list_agents()}
        names_b = {r["name"] for r in app_b.state.taosmd_agent_registry.list_agents()}
        assert names_a == {"agent-a-only"}
        assert names_b == set()

        app_b.state.taosmd_agent_registry.register_agent("agent-b-only")
        names_a = {r["name"] for r in app_a.state.taosmd_agent_registry.list_agents()}
        names_b = {r["name"] for r in app_b.state.taosmd_agent_registry.list_agents()}
        assert names_a == {"agent-a-only"}
        assert names_b == {"agent-b-only"}
