import pytest


class TestChannelHubRoutes:
    @pytest.mark.asyncio
    async def test_hub_status(self, client):
        resp = await client.get("/api/channel-hub/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "connectors" in data
        assert "adapters" in data

    @pytest.mark.asyncio
    async def test_list_adapters(self, client):
        resp = await client.get("/api/channel-hub/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert "adapters" in data

    @pytest.mark.asyncio
    async def test_connect_webchat(self, client):
        resp = await client.post(
            "/api/channel-hub/connect",
            content='{"platform": "webchat", "agent_name": "test-agent"}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "connected"

    @pytest.mark.asyncio
    async def test_disconnect(self, client):
        await client.post(
            "/api/channel-hub/connect",
            content='{"platform": "webchat", "agent_name": "disc-test"}',
            headers={"content-type": "application/json"},
        )
        resp = await client.post(
            "/api/channel-hub/disconnect",
            content='{"platform": "webchat", "agent_name": "disc-test"}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disconnected"
