import pytest


class TestAuthRoutes:
    @pytest.mark.asyncio
    async def test_login_page(self, client):
        resp = await client.get("/auth/login")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_login_no_password_configured(self, client):
        resp = await client.post("/auth/login", data={"password": "anything"})
        assert resp.status_code in (200, 303)

    @pytest.mark.asyncio
    async def test_health_exempt_from_auth(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_cluster_register_exempt_from_session_auth(self, client):
        # POST /api/cluster/workers is session-exempt (no session cookie required),
        # but the route-level HMAC gate rejects unpaired workers with 401.
        # This verifies that the session middleware lets the request through
        # (i.e. the route itself is reached and returns its own 401 code).
        resp = await client.post("/api/cluster/workers", json={
            "name": "test-worker",
            "url": "http://localhost:9090",
            "platform": "linux",
            "capabilities": [],
            "hardware": {},
        })
        assert resp.status_code == 401
        assert resp.json().get("code") == "worker_not_paired"
