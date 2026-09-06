import pytest


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health_returns_ok(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data


@pytest.mark.asyncio
class TestDashboardPage:
    async def test_root_redirects_to_desktop(self, client):
        # Root redirects to /desktop (the new desktop shell)
        resp = await client.get("/", follow_redirects=True)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "taOS" in resp.text

    async def test_backends_api(self, client):
        resp = await client.get("/api/backends")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "backends" in data
        assert isinstance(data["backends"], list)
        assert "primary" in data
        assert "fallback_status" in data

    async def test_metrics_api(self, client):
        resp = await client.get("/api/metrics/test.metric?range=24h")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
class TestClusterSummary:
    async def test_cluster_summary_endpoint(self, client):
        resp = await client.get("/api/dashboard/cluster-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "workers" in data
        assert "online" in data
        assert "total_ram_gb" in data
        assert "total_vram_gb" in data
        assert data["workers"] == 0


class TestActivityFeed:
    @pytest.mark.asyncio
    async def test_activity_json(self, client):
        resp = await client.get("/api/dashboard/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert isinstance(data["events"], list)

    @pytest.mark.asyncio
    async def test_activity_with_events(self, client):
        # Add a notification first
        notif_store = client._transport.app.state.notifications
        await notif_store.add("Test event", "Something happened", level="info", source="test")
        resp = await client.get("/api/dashboard/activity")
        data = resp.json()
        assert len(data["events"]) >= 1
        assert data["events"][0]["title"] == "Test event"
