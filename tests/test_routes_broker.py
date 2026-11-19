import pytest
from unittest.mock import AsyncMock, patch
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

    async def _active_keenable_grant(self, client, agent="test-agent", scopes=None):
        """Request and approve an active keenable grant for *agent* with *scopes*.

        Uses the `client` fixture, which initialises the broker store exactly
        as conftest does, then approves the request so the grant is active.
        """
        scopes = scopes or ["search", "fetch"]
        resp = await client.post(
            "/api/broker/request",
            json={"provider_id": "keenable", "agent_identity": agent, "scopes": scopes},
        )
        assert resp.status_code == 200, resp.text
        grant_id = resp.json()["grant_id"]
        resp = await client.post(
            f"/api/broker/grants/{grant_id}/approve", json={"window_seconds": 3600}
        )
        assert resp.status_code == 200, resp.text
        return grant_id

    async def _agent_client(self, app, agent_name="test-agent"):
        """ASGI client authenticated with a bound per-agent local token.

        The broker store on *app* must already be initialised (the `client`
        fixture does this); this helper only adds the auth header.
        """
        agent_token = app.state.auth.mint_agent_local_token(agent_name)
        transport = ASGITransport(app=app)
        return AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {agent_token}"},
        )

    async def test_keenable_search_empty_query_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": ""})
        assert resp.status_code == 400
        assert resp.json()["error"] == "query must not be empty"

    async def test_keenable_search_query_too_long_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "x" * 501})
        assert resp.status_code == 400
        assert resp.json()["error"] == "query must be at most 500 characters"

    async def test_keenable_search_num_results_below_range_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test", "num_results": 0})
        assert resp.status_code == 400
        assert resp.json()["error"] == "num_results must be between 1 and 10"

    async def test_keenable_search_num_results_above_range_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test", "num_results": 11})
        assert resp.status_code == 400
        assert resp.json()["error"] == "num_results must be between 1 and 10"

    async def test_keenable_search_num_results_non_int_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test", "num_results": "five"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "num_results must be an integer"

    async def test_keenable_search_num_results_boolean_400(self, client, app):
        """`isinstance(True, int)` is true, so booleans must be rejected explicitly."""
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post(
                "/api/broker/keenable/search", json={"query": "test", "num_results": True}
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "num_results must be an integer"

    async def test_keenable_fetch_invalid_url_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/fetch", json={"url": "ftp://example.com"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "url must start with http:// or https://"

    async def test_keenable_fetch_max_chars_below_range_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/fetch", json={"url": "https://example.com", "max_chars": 0})
        assert resp.status_code == 400
        assert resp.json()["error"] == "max_chars must be between 1 and 100000"

    async def test_keenable_fetch_max_chars_above_range_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/fetch", json={"url": "https://example.com", "max_chars": 100001})
        assert resp.status_code == 400
        assert resp.json()["error"] == "max_chars must be between 1 and 100000"

    async def test_keenable_fetch_max_chars_non_int_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/fetch", json={"url": "https://example.com", "max_chars": "large"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "max_chars must be an integer"

    async def test_keenable_fetch_url_non_string_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/fetch", json={"url": 123})
        assert resp.status_code == 400
        assert resp.json()["error"] == "url must be a string"

    async def test_keenable_search_query_non_string_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": 123})
        assert resp.status_code == 400
        assert resp.json()["error"] == "query must be a string"

    async def test_keenable_search_no_agent_token_403(self, client, app):
        """The host local token is authenticated but not bound to any agent."""
        token = app.state.auth.get_local_token()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "agent token required"

    async def test_keenable_search_no_grant_403(self, client, app):
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "no active keenable grant"

    async def test_keenable_search_wrong_scope_403(self, client, app):
        await self._active_keenable_grant(client, scopes=["fetch"])
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "scope not granted: search"

    async def test_keenable_fetch_wrong_scope_403(self, client, app):
        await self._active_keenable_grant(client, scopes=["search"])
        async with await self._agent_client(app) as c:
            resp = await c.post(
                "/api/broker/keenable/fetch", json={"url": "https://example.com"}
            )
        assert resp.status_code == 403
        assert resp.json()["error"] == "scope not granted: fetch"

    async def test_keenable_search_not_configured_409(self, client, app):
        """No KEENABLE_API_KEY secret and public fallback off -> 409 before any upstream call."""
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "keenable not configured"

    async def test_keenable_search_success_200(self, client, app):
        await self._active_keenable_grant(client)
        await app.state.secrets.add("KEENABLE_API_KEY", "test-key")
        expected = [{"title": "needle in a haystack", "url": "https://example.com/needle"}]
        with patch(
            "tinyagentos.routes.broker.keenable_search",
            AsyncMock(return_value=expected),
        ) as mock_search:
            async with await self._agent_client(app) as c:
                resp = await c.post(
                    "/api/broker/keenable/search", json={"query": "needle", "num_results": 3}
                )
        assert resp.status_code == 200
        assert resp.json() == {"results": expected}
        mock_search.assert_awaited_once_with("test-key", "needle", 3, allow_public=False)

    async def test_keenable_fetch_success_200(self, client, app):
        await self._active_keenable_grant(client)
        await app.state.secrets.add("KEENABLE_API_KEY", "test-key")
        expected = {"text": "<html>page</html>", "url": "https://example.com/page"}
        with patch(
            "tinyagentos.routes.broker.keenable_fetch",
            AsyncMock(return_value=expected),
        ) as mock_fetch:
            async with await self._agent_client(app) as c:
                resp = await c.post(
                    "/api/broker/keenable/fetch",
                    json={"url": "https://example.com/page", "max_chars": 500},
                )
        assert resp.status_code == 200
        assert resp.json() == expected
        mock_fetch.assert_awaited_once_with("test-key", "https://example.com/page", 500, allow_public=False)


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
