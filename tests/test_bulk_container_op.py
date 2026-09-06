import pytest
from tinyagentos.routes.agents import _bulk_container_op


class FakeConfig:
    def __init__(self, agents):
        self.agents = agents


@pytest.mark.asyncio
class TestBulkContainerOp:
    async def test_all_agents_succeed(self):
        config = FakeConfig([{"name": "alpha"}, {"name": "beta"}])

        async def op(container_name):
            return {"success": True}

        results = await _bulk_container_op(config, op)
        assert results == {
            "alpha": {"success": True},
            "beta": {"success": True},
        }

    async def test_op_returns_success_false(self):
        config = FakeConfig([{"name": "alpha"}])

        async def op(container_name):
            return {"success": False}

        results = await _bulk_container_op(config, op)
        assert results == {"alpha": {"success": False}}
        assert "error" not in results["alpha"]

    async def test_op_raises_for_one_agent_continues(self):
        config = FakeConfig([{"name": "alpha"}, {"name": "beta"}, {"name": "gamma"}])

        async def op(container_name):
            if "beta" in container_name:
                raise RuntimeError("container not found")
            return {"success": True}

        results = await _bulk_container_op(config, op)
        assert results["alpha"] == {"success": True}
        assert results["beta"] == {"success": False, "error": "container not found"}
        assert results["gamma"] == {"success": True}

    async def test_empty_agents(self):
        config = FakeConfig([])

        async def op(container_name):
            return {"success": True}

        results = await _bulk_container_op(config, op)
        assert results == {}

    async def test_container_name_format(self):
        config = FakeConfig([{"name": "my-agent"}])
        received = []

        async def op(container_name):
            received.append(container_name)
            return {"success": True}

        await _bulk_container_op(config, op)
        assert received == ["taos-agent-my-agent"]
