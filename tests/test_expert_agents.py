import pytest
from tinyagentos.expert_agents import ExpertAgentStore


class TestExpertAgentStore:
    @pytest.mark.asyncio
    async def test_get_or_create(self, tmp_path):
        store = ExpertAgentStore(tmp_path / "experts.db")
        await store.init()
        try:
            agent = await store.get_or_create(
                "blender", "Blender Expert", "You are a Blender expert", "qwen3-4b", "#E87D0D", "optional"
            )
            assert agent["app_id"] == "blender"
            assert agent["name"] == "Blender Expert"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_or_create_idempotent(self, tmp_path):
        store = ExpertAgentStore(tmp_path / "experts.db")
        await store.init()
        try:
            a1 = await store.get_or_create("blender", "Blender Expert", "prompt1", "qwen3-4b", "#E87D0D", "optional")
            a2 = await store.get_or_create("blender", "Blender Expert", "prompt2", "qwen3-4b", "#E87D0D", "optional")
            assert a1["id"] == a2["id"]
            # Should NOT update prompt on second call
            assert a2["system_prompt"] == "prompt1"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_by_app(self, tmp_path):
        store = ExpertAgentStore(tmp_path / "experts.db")
        await store.init()
        try:
            await store.get_or_create("gimp", "GIMP Expert", "You are a GIMP expert", "qwen3-4b", "#000", "optional")
            agent = await store.get_by_app("gimp")
            assert agent is not None
            assert agent["name"] == "GIMP Expert"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_by_app_nonexistent(self, tmp_path):
        store = ExpertAgentStore(tmp_path / "experts.db")
        await store.init()
        try:
            assert await store.get_by_app("nonexistent") is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_prompt(self, tmp_path):
        store = ExpertAgentStore(tmp_path / "experts.db")
        await store.init()
        try:
            await store.get_or_create("blender", "Blender Expert", "old prompt", "qwen3-4b", "#E87D0D", "optional")
            await store.update_prompt("blender", "new prompt")
            agent = await store.get_by_app("blender")
            assert agent["system_prompt"] == "new prompt"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_reset(self, tmp_path):
        store = ExpertAgentStore(tmp_path / "experts.db")
        await store.init()
        try:
            await store.get_or_create("blender", "Blender Expert", "prompt", "qwen3-4b", "#E87D0D", "optional")
            await store.reset("blender")
            assert await store.get_by_app("blender") is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_list_all(self, tmp_path):
        store = ExpertAgentStore(tmp_path / "experts.db")
        await store.init()
        try:
            await store.get_or_create("blender", "Blender Expert", "p1", "qwen3-4b", "#E87D0D", "optional")
            await store.get_or_create("gimp", "GIMP Expert", "p2", "qwen3-4b", "#000", "optional")
            agents = await store.list_all()
            assert len(agents) == 2
        finally:
            await store.close()
