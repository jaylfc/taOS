from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.broker.clients.keenable import public_fallback_enabled
from tinyagentos.routes.broker import _get_keenable_user_id


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

    async def test_keenable_search_after_revoke_403(self, client, app):
        grant_id = await self._active_keenable_grant(client)
        resp = await client.post(f"/api/broker/grants/{grant_id}/revoke")
        assert resp.status_code == 200
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json={"query": "test"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "no active keenable grant"

    async def test_keenable_fetch_not_configured_409_client_not_called(self, client, app):
        await self._active_keenable_grant(client)
        with patch("tinyagentos.routes.broker.keenable_fetch", AsyncMock()) as mock_fetch:
            async with await self._agent_client(app) as c:
                resp = await c.post(
                    "/api/broker/keenable/fetch", json={"url": "https://example.com"}
                )
        assert resp.status_code == 409
        assert resp.json()["error"] == "keenable not configured"
        mock_fetch.assert_not_awaited()

    async def test_keenable_search_public_fallback_200_without_key(self, client, app):
        await self._active_keenable_grant(client)
        with patch(
            "tinyagentos.routes.broker.public_fallback_enabled",
            AsyncMock(return_value=True),
        ), patch(
            "tinyagentos.routes.broker.keenable_search",
            AsyncMock(return_value=[]),
        ) as mock_search:
            async with await self._agent_client(app) as c:
                resp = await c.post("/api/broker/keenable/search", json={"query": "test"})
        assert resp.status_code == 200
        mock_search.assert_awaited_once_with("", "test", 5, allow_public=True)

    async def test_keenable_search_upstream_error_502_hides_detail(self, client, app):
        await self._active_keenable_grant(client)
        await app.state.secrets.add("KEENABLE_API_KEY", "secret-key-value")
        with patch(
            "tinyagentos.routes.broker.keenable_search",
            AsyncMock(side_effect=RuntimeError("boom secret-key-value")),
        ):
            async with await self._agent_client(app) as c:
                resp = await c.post("/api/broker/keenable/search", json={"query": "test"})
        assert resp.status_code == 502
        assert "boom" not in resp.text
        assert "secret-key-value" not in resp.text

    async def test_keenable_search_non_object_body_400(self, client, app):
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post("/api/broker/keenable/search", json=["query"])
        assert resp.status_code == 400
        assert resp.json()["error"] == "body must be a JSON object"

    async def test_keenable_fetch_max_chars_boolean_400(self, client, app):
        """`isinstance(True, int)` is true, so booleans must be rejected explicitly."""
        await self._active_keenable_grant(client)
        async with await self._agent_client(app) as c:
            resp = await c.post(
                "/api/broker/keenable/fetch",
                json={"url": "https://example.com", "max_chars": True},
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "max_chars must be an integer"


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


# ---------------------------------------------------------------------------
# Per-person broker preferences: resolve the signed-in caller, never "user".
#
# These tests exercise _get_keenable_user_id directly (the keenable routes are
# agent-bound, so a session caller is simulated with a request object) and then
# confirm public_fallback_enabled reads the resolved caller's own preference.
# ---------------------------------------------------------------------------


def _add_member(app, username: str) -> str:
    """Inject a non-admin member into the auth store and return their user_id."""
    auth = app.state.auth
    invite_code = auth.add_user_invite(username, invited_by_username="admin")
    auth.complete_invite(
        username=username,
        invite_code=invite_code,
        full_name=username.title(),
        email=f"{username}@test.local",
        password=f"{username}pass1",
    )
    return auth.find_user(username)["id"]


class _FakeState:
    """Minimal request.state stand-in for unit-testing _get_keenable_user_id."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeRequest:
    """Minimal request stand-in carrying app state and request.state."""

    def __init__(self, app, *, user_id=None, agent_name=None, via=None):
        self.app = app
        self.state = _FakeState(user_id=user_id, agent_name=agent_name, via=via)


@pytest_asyncio.fixture
async def broker_app(tmp_path):
    """App with desktop_settings initialised, an admin, and members alice/bob."""
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    await app.state.desktop_settings.init()
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _add_member(app, "alice")
    _add_member(app, "bob")
    app.state._startup_complete = True
    yield app
    await app.state.desktop_settings.close()


@pytest.mark.asyncio
class TestKeeenablePerPersonUserId:
    """_get_keenable_user_id must resolve the signed-in caller, never "user"."""

    async def _pref(self, app, user_id: str, value: bool) -> None:
        await app.state.desktop_settings.save_preference(
            user_id, "broker", {"keenable_public_fallback": value}
        )

    async def test_session_caller_reads_own_preference(self, broker_app):
        """Two members with different broker preferences each read their own."""
        app = broker_app
        alice_uid = app.state.auth.find_user("alice")["id"]
        bob_uid = app.state.auth.find_user("bob")["id"]
        await self._pref(app, alice_uid, True)
        await self._pref(app, bob_uid, False)

        req_alice = _FakeRequest(app, user_id=alice_uid, via="session")
        req_bob = _FakeRequest(app, user_id=bob_uid, via="session")
        assert await _get_keenable_user_id(req_alice) == alice_uid
        assert await _get_keenable_user_id(req_bob) == bob_uid
        # Each request reads ITS caller's preference, never a shared scope.
        assert await public_fallback_enabled(
            app.state.desktop_settings, await _get_keenable_user_id(req_alice)
        ) is True
        assert await public_fallback_enabled(
            app.state.desktop_settings, await _get_keenable_user_id(req_bob)
        ) is False

    async def test_agent_caller_reads_owner_preference(self, broker_app):
        """An agent's request reads its owner's preference."""
        app = broker_app
        owner_uid = app.state.auth.find_user("admin")["id"]
        await self._pref(app, owner_uid, True)
        with patch(
            "tinyagentos.routes.broker.resolve_agent_owner",
            AsyncMock(return_value=owner_uid),
        ):
            req = _FakeRequest(app, agent_name="helper-1", via="local_token")
            assert await _get_keenable_user_id(req) == owner_uid
            assert await public_fallback_enabled(
                app.state.desktop_settings, owner_uid
            ) is True

    async def test_unknown_caller_gets_system_default(self, broker_app):
        """An unknown caller reads the shipped system default (primary owner)."""
        app = broker_app
        primary_uid = app.state.auth.get_primary_user()["id"]
        await self._pref(app, primary_uid, True)
        req = _FakeRequest(app)  # no session user, no agent bound
        assert await _get_keenable_user_id(req) == primary_uid
        assert await public_fallback_enabled(
            app.state.desktop_settings, primary_uid
        ) is True
