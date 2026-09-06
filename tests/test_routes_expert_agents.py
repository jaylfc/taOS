import pytest


class TestExpertAgentRoutes:
    @pytest.mark.asyncio
    async def test_list_experts_empty(self, client):
        resp = await client.get("/api/streaming-apps/experts")
        assert resp.status_code == 200
        assert resp.json()["experts"] == []

    @pytest.mark.asyncio
    async def test_get_expert_not_found(self, client):
        resp = await client.get("/api/streaming-apps/experts/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reset_expert_not_found(self, client):
        resp = await client.post("/api/streaming-apps/experts/nonexistent/reset")
        assert resp.status_code == 404
