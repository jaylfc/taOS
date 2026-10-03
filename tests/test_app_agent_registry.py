import pytest
from pathlib import Path

from tinyagentos.app import create_app


class TestAppAgentRegistry:
    @pytest.mark.asyncio
    async def test_create_app_roots_registry_at_data_dir(self, tmp_path):
        app_a = create_app(data_dir=tmp_path / "a")
        app_b = create_app(data_dir=tmp_path / "b")

        assert app_a.state.agent_registry is not None
        assert app_b.state.agent_registry is not None
        assert app_a.state.agent_registry is not app_b.state.agent_registry
        assert app_a.state.agent_registry.registry_path == tmp_path / "a" / "agents.json"
        assert app_b.state.agent_registry.registry_path == tmp_path / "b" / "agents.json"
