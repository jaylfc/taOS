import pytest


@pytest.mark.asyncio
class TestPatchAgentPersona:
    async def test_patch_persona_updates_both_fields(self, client, app):
        app.state.config.agents.append({
            "name": "atlas",
            "display_name": "Atlas",
            "host": "",
            "color": "#888888",
            "soul_md": "",
            "agent_md": "",
        })
        resp = await client.patch("/api/agents/atlas/persona", json={
            "soul_md": "new soul",
            "agent_md": "new rules",
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == "atlas")
        assert agent["soul_md"] == "new soul"
        assert agent["agent_md"] == "new rules"

    async def test_patch_persona_partial(self, client, app):
        app.state.config.agents.append({
            "name": "atlas",
            "display_name": "Atlas",
            "host": "",
            "color": "#888888",
            "soul_md": "",
            "agent_md": "",
        })
        await client.patch("/api/agents/atlas/persona", json={"soul_md": "set soul"})
        await client.patch("/api/agents/atlas/persona", json={"agent_md": "only this"})
        agent = next(a for a in app.state.config.agents if a["name"] == "atlas")
        assert agent["soul_md"] == "set soul"
        assert agent["agent_md"] == "only this"

    async def test_patch_persona_with_source_updates_provenance(self, client, app):
        app.state.config.agents.append({
            "name": "atlas",
            "display_name": "Atlas",
            "host": "",
            "color": "#888888",
            "soul_md": "",
            "agent_md": "",
        })
        resp = await client.patch("/api/agents/atlas/persona", json={
            "soul_md": "swap",
            "source_persona_id": "builtin:support",
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == "atlas")
        assert agent["source_persona_id"] == "builtin:support"

    async def test_patch_persona_returns_404_for_unknown_agent(self, client, app):
        resp = await client.patch("/api/agents/no-such-agent/persona", json={"soul_md": "x"})
        assert resp.status_code == 404
