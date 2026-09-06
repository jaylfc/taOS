import pytest
from httpx import ASGITransport, AsyncClient


async def _request_grant(client, provider_id="exa", agent="agent-x", **kw):
    body = {"provider_id": provider_id, "agent_identity": agent, **kw}
    resp = await client.post("/api/broker/request", json=body)
    return resp


@pytest.mark.asyncio
class TestBrokerRoutes:
    async def test_providers_lists_seeded(self, client):
        resp = await client.get("/api/broker/providers")
        assert resp.status_code == 200
        providers = resp.json()
        ids = {p["id"] for p in providers}
        assert "exa" in ids
        assert "openai" in ids
        exa = next(p for p in providers if p["id"] == "exa")
        assert exa["kind"] == "mcp"
        assert exa["default_scopes"] == ["search"]

    async def test_request_creates_pending_and_notifies(self, client, app):
        resp = await _request_grant(client, reason="needs search")
        assert resp.status_code == 200
        grant_id = resp.json()["grant_id"]
        assert grant_id

        grants = (await client.get("/api/broker/grants")).json()
        match = next(g for g in grants if g["id"] == grant_id)
        assert match["status"] == "pending"

        notifs = await app.state.notifications.list()
        broker_notifs = [n for n in notifs if n["source"] == "broker"]
        assert len(broker_notifs) >= 1
        assert "agent-x" in broker_notifs[0]["message"]
        assert "needs search" in broker_notifs[0]["message"]

    async def test_request_unknown_provider_400(self, client):
        resp = await _request_grant(client, provider_id="does-not-exist")
        assert resp.status_code == 400
        assert "does-not-exist" in resp.json()["error"]

    async def test_request_missing_agent_identity_400(self, client):
        resp = await client.post("/api/broker/request", json={"provider_id": "exa"})
        assert resp.status_code == 400
        assert "agent_identity" in resp.json()["error"]

    async def test_approve_makes_grant_active(self, client):
        grant_id = (await _request_grant(client)).json()["grant_id"]
        resp = await client.post(
            f"/api/broker/grants/{grant_id}/approve", json={"window_seconds": 3600}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

        active = (await client.get("/api/broker/grants?status=active")).json()
        assert any(g["id"] == grant_id for g in active)

    async def test_approve_bad_window_400(self, client):
        grant_id = (await _request_grant(client)).json()["grant_id"]
        resp = await client.post(
            f"/api/broker/grants/{grant_id}/approve", json={"window_seconds": 0}
        )
        assert resp.status_code == 400

    async def test_approve_unknown_grant_400(self, client):
        resp = await client.post(
            "/api/broker/grants/nope/approve", json={"window_seconds": 60}
        )
        assert resp.status_code == 400

    async def test_http_proxy_approve_mints_token(self, client):
        grant_id = (await _request_grant(client, provider_id="openai")).json()["grant_id"]
        resp = await client.post(
            f"/api/broker/grants/{grant_id}/approve", json={"window_seconds": 3600}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("token")
        assert data["token"].startswith("sk-taos-secret-")

    async def test_mcp_approve_no_token(self, client):
        grant_id = (await _request_grant(client, provider_id="exa")).json()["grant_id"]
        resp = await client.post(
            f"/api/broker/grants/{grant_id}/approve", json={"window_seconds": 3600}
        )
        assert resp.status_code == 200
        assert "token" not in resp.json()

    async def test_deny(self, client):
        grant_id = (await _request_grant(client)).json()["grant_id"]
        resp = await client.post(f"/api/broker/grants/{grant_id}/deny")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        grants = (await client.get("/api/broker/grants")).json()
        match = next(g for g in grants if g["id"] == grant_id)
        assert match["status"] == "denied"

    async def test_revoke(self, client):
        grant_id = (await _request_grant(client)).json()["grant_id"]
        await client.post(
            f"/api/broker/grants/{grant_id}/approve", json={"window_seconds": 3600}
        )
        resp = await client.post(f"/api/broker/grants/{grant_id}/revoke")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        grants = (await client.get("/api/broker/grants")).json()
        match = next(g for g in grants if g["id"] == grant_id)
        assert match["status"] == "revoked"

    async def test_grants_filter_by_agent(self, client):
        await _request_grant(client, agent="agent-a")
        await _request_grant(client, agent="agent-b")
        grants = (await client.get("/api/broker/grants?agent_identity=agent-a")).json()
        assert grants
        assert all(g["agent_identity"] == "agent-a" for g in grants)

    async def test_requires_auth(self, app):
        # No taos_session cookie -> the session guard middleware rejects the
        # request, same as it does for the secrets routes.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/broker/providers")
        assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_deny_revoke_unknown_grant_404(client):
    """deny/revoke on an unknown grant id return 404 (consistent with approve)."""
    r1 = await client.post("/api/broker/grants/tsk-nope/deny")
    assert r1.status_code == 404
    r2 = await client.post("/api/broker/grants/tsk-nope/revoke")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_approve_denied_grant_400(client):
    """A denied grant cannot be resurrected via the approve route."""
    gid = (await _request_grant(client)).json()["grant_id"]
    deny = await client.post(f"/api/broker/grants/{gid}/deny")
    assert deny.status_code == 200
    resp = await client.post(
        f"/api/broker/grants/{gid}/approve", json={"window_seconds": 3600}
    )
    assert resp.status_code == 400
