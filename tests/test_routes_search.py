import pytest


class TestGlobalSearch:
    @pytest.mark.asyncio
    async def test_search_agents(self, client):
        resp = await client.get("/api/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "query" in data
        # Should find "test-agent" from config
        agent_results = [r for r in data["results"] if r["type"] == "agent"]
        assert len(agent_results) >= 1
        assert agent_results[0]["title"] == "test-agent"

    @pytest.mark.asyncio
    async def test_search_apps(self, client):
        resp = await client.get("/api/search?q=smol")
        assert resp.status_code == 200
        data = resp.json()
        app_results = [r for r in data["results"] if r["type"] == "app"]
        assert len(app_results) >= 1

    @pytest.mark.asyncio
    async def test_search_empty_query(self, client):
        resp = await client.get("/api/search?q=")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    @pytest.mark.asyncio
    async def test_search_short_query(self, client):
        resp = await client.get("/api/search?q=a")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    @pytest.mark.asyncio
    async def test_search_no_matches(self, client):
        resp = await client.get("/api/search?q=zzzznonexistent")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 0
