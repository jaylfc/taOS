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


# --- Login lockout footgun (#135): a correct password must never be refused by
# the brute-force limiter, or a locked-out user gets funneled into a new account.

@pytest.mark.asyncio
async def test_correct_password_succeeds_after_soft_lockout(client):
    from tinyagentos.routes.auth import _login_limiter
    _login_limiter._log.clear()
    for _ in range(6):
        await client.post("/auth/login", json={"password": "wrong"})
    # Soft-locked now; the CORRECT password must still sign in.
    resp = await client.post("/auth/login", json={"password": "testpass"})
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


@pytest.mark.asyncio
async def test_wrong_password_locks_out_after_soft_limit(client):
    from tinyagentos.routes.auth import _login_limiter
    _login_limiter._log.clear()
    last = None
    for _ in range(6):
        last = await client.post("/auth/login", json={"password": "wrong"})
    assert last.status_code == 429
    assert "Too many" in last.json().get("error", "")


@pytest.mark.asyncio
async def test_hard_ceiling_blocks_even_correct_password(client, monkeypatch):
    from tinyagentos.routes.auth import _login_limiter
    monkeypatch.setattr("tinyagentos.routes.auth._LOGIN_HARD_MAX", 3)
    _login_limiter._log.clear()
    for _ in range(3):
        await client.post("/auth/login", json={"password": "wrong"})
    # At/over the hard ceiling we reject before verifying, even a correct one.
    resp = await client.post("/auth/login", json={"password": "testpass"})
    assert resp.status_code == 429
