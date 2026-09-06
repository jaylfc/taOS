import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
class TestDeployPersonaFields:
    async def test_deploy_stores_persona_fields(self, client, app):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{"x": 1}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "Atlas-persona",
            "framework": "openclaw",
            "soul_md": "You are Atlas",
            "agent_md": "Always verify",
            "memory_plugin": "taosmd",
            "source_persona_id": "builtin:research",
        })
        assert resp.status_code == 200
        slug = resp.json()["name"]
        agent = next(a for a in app.state.config.agents if a["name"] == slug)
        assert agent["soul_md"] == "You are Atlas"
        assert agent["agent_md"] == "Always verify"
        assert agent["memory_plugin"] == "taosmd"
        assert agent["source_persona_id"] == "builtin:research"
        assert agent["migrated_to_v2_personas"] is True
        assert agent["display_name"] == "Atlas-persona"

    async def test_deploy_persona_fields_default_correctly(self, client, app):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={"name": "DefaultAgent", "framework": "openclaw"})
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == resp.json()["name"])
        assert agent["soul_md"] == ""
        assert agent["agent_md"] == ""
        assert agent["memory_plugin"] == "taosmd"
        assert agent["source_persona_id"] is None
        assert agent["migrated_to_v2_personas"] is True

    async def test_deploy_with_save_to_library_writes_user_persona(self, client, app, tmp_path):
        from tinyagentos.user_personas import UserPersonaStore
        if not hasattr(app.state, "user_personas"):
            app.state.user_personas = UserPersonaStore(tmp_path / "p.db")
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "CustomOne",
            "framework": "openclaw",
            "soul_md": "Custom soul",
            "agent_md": "",
            "memory_plugin": "taosmd",
            "save_to_library": {"name": "My Custom", "description": "for reuse"},
        })
        assert resp.status_code == 200
        rows = app.state.user_personas.list()
        assert any(r["name"] == "My Custom" and r["soul_md"] == "Custom soul" for r in rows)

    async def test_deploy_without_save_to_library_does_not_create_persona(self, client, app, tmp_path):
        from tinyagentos.user_personas import UserPersonaStore
        if not hasattr(app.state, "user_personas"):
            app.state.user_personas = UserPersonaStore(tmp_path / "p.db")
        before = len(app.state.user_personas.list())
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "NoSave",
            "framework": "openclaw",
            "soul_md": "S",
        })
        assert resp.status_code == 200
        after = len(app.state.user_personas.list())
        assert after == before

    async def test_deploy_registers_agent_with_taosmd(self, client, app, monkeypatch):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        calls = []
        def fake_register(name, **kwargs):
            calls.append(name)
        import taosmd.agents as tm_agents
        monkeypatch.setattr(tm_agents, "register_agent", fake_register)
        resp = await client.post("/api/agents/deploy", json={"name": "Atlas-reg", "framework": "openclaw"})
        assert resp.status_code == 200
        assert calls == [resp.json()["name"]]

    async def test_deploy_aborts_if_register_agent_fails(self, client, app, monkeypatch):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        def fake_register(name, **kwargs):
            raise RuntimeError("taosmd down")
        import taosmd.agents as tm_agents
        monkeypatch.setattr(tm_agents, "register_agent", fake_register)
        before_count = len(app.state.config.agents)
        resp = await client.post("/api/agents/deploy", json={"name": "Atlas-fail", "framework": "openclaw"})
        assert resp.status_code == 500
        assert len(app.state.config.agents) == before_count

    async def test_deploy_tolerates_already_registered(self, client, app, monkeypatch):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        import taosmd.agents as tm_agents
        def fake_register(name, **kwargs):
            raise tm_agents.AgentExistsError(name)
        monkeypatch.setattr(tm_agents, "register_agent", fake_register)
        resp = await client.post("/api/agents/deploy", json={"name": "Atlas-dup", "framework": "openclaw"})
        assert resp.status_code == 200
