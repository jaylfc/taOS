import pytest


@pytest.mark.asyncio
class TestDismissMigrationBanner:
    async def test_dismiss_banner_sets_flag(self, client, app):
        app.state.config.agents.append({
            "name": "legacy-bot",
            "display_name": "Legacy Bot",
            "host": "",
            "color": "#888888",
            "migrated_to_v2_personas": False,
        })
        resp = await client.post("/api/agents/legacy-bot/dismiss-migration-banner")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        agent = next(a for a in app.state.config.agents if a["name"] == "legacy-bot")
        assert agent["migrated_to_v2_personas"] is True

    async def test_dismiss_banner_404_unknown_agent(self, client):
        resp = await client.post("/api/agents/no-such-agent/dismiss-migration-banner")
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]
