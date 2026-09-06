import pytest


@pytest.mark.asyncio
class TestPatchAgentMemory:
    async def test_patch_memory_toggles_plugin(self, client, app):
        app.state.config.agents.append({
            "name": "atlas",
            "display_name": "Atlas",
            "host": "",
            "color": "#888888",
            "soul_md": "",
            "agent_md": "",
            "memory_plugin": "taosmd",
        })
        resp = await client.patch("/api/agents/atlas/memory", json={"memory_plugin": "none"})
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == "atlas")
        assert agent["memory_plugin"] == "none"

        resp2 = await client.patch("/api/agents/atlas/memory", json={"memory_plugin": "taosmd"})
        assert resp2.status_code == 200
        assert agent["memory_plugin"] == "taosmd"

    async def test_patch_memory_rejects_unknown_plugin(self, client, app):
        app.state.config.agents.append({
            "name": "atlas",
            "display_name": "Atlas",
            "host": "",
            "color": "#888888",
            "soul_md": "",
            "agent_md": "",
            "memory_plugin": "taosmd",
        })
        resp = await client.patch("/api/agents/atlas/memory", json={"memory_plugin": "weaviate"})
        assert resp.status_code == 400

    async def test_patch_memory_returns_404_for_unknown_agent(self, client, app):
        resp = await client.patch("/api/agents/no-such-agent/memory", json={"memory_plugin": "none"})
        assert resp.status_code == 404

    async def test_patch_memory_mode(self, client, app):
        app.state.config.agents.append({
            "name": "atlas",
            "display_name": "Atlas",
            "host": "",
            "color": "#888888",
            "soul_md": "",
            "agent_md": "",
            "memory_plugin": "taosmd",
            "memory_mode": "both",
        })
        resp = await client.patch("/api/agents/atlas/memory", json={"memory_plugin": "taosmd", "memory_mode": "framework"})
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == "atlas")
        assert agent["memory_mode"] == "framework"

    async def test_patch_memory_mode_rejects_invalid(self, client, app):
        app.state.config.agents.append({
            "name": "atlas",
            "display_name": "Atlas",
            "host": "",
            "color": "#888888",
            "soul_md": "",
            "agent_md": "",
            "memory_plugin": "taosmd",
        })
        resp = await client.patch("/api/agents/atlas/memory", json={"memory_plugin": "taosmd", "memory_mode": "invalid"})
        assert resp.status_code == 400
