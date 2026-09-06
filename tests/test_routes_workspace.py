import pytest


class TestWorkspaceRoutes:
    @pytest.mark.asyncio
    async def test_send_inter_agent_message(self, client):
        resp = await client.post("/api/agents/test-agent/messages", json={
            "from_agent": "other-agent",
            "message": "Hello from test",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_contacts(self, client):
        resp = await client.get("/api/agents/test-agent/workspace/messages/contacts")
        assert resp.status_code == 200
