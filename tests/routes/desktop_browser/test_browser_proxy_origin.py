"""Tests for the browser-proxy origin (frontend win #2, part 1).

The proxy origin is a SECOND, API-free, token-authenticated ASGI app that
shares the main app's state. These tests prove:

  * It serves only the proxy fetch + /__taos/sw.js + /__taos/redeem and
    returns 404 for taOS API/auth routes (it is API-free).
  * The proxy route requires the taos_browser cookie (401 without; 200 with
    a cookie minted via a valid redeem; upstream fetch mocked).
  * /__taos/redeem with a valid ticket sets the cookie + 302s to `next`;
    invalid/expired ticket -> 403; off-origin `next` -> rejected.
  * The main app's /api/desktop/browser/proxy-ticket endpoint requires auth
    and returns a token.
"""
from __future__ import annotations

import os
from unittest.mock import patch
from urllib.parse import quote

import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response

from tinyagentos.browser_proxy_origin import create_browser_proxy_app
from tinyagentos.routes.desktop_browser.proxy_ticket import mint_proxy_ticket


@pytest_asyncio.fixture
async def proxy_setup(client, app):
    """Build the proxy-origin app sharing the (already-initialised) main
    app state, plus a clients pair and a shared signing key.

    The `client` fixture initialises all the stores on `app.state` and
    configures the admin user; we reuse that state for the proxy app.
    """
    # Ensure a shared signing key exists on the shared state.
    signing_key = os.urandom(32)
    app.state.browser_proxy_signing_key = signing_key

    proxy_app = create_browser_proxy_app(app.state)
    transport = ASGITransport(app=proxy_app)
    async with AsyncClient(transport=transport, base_url="http://proxy") as pc:
        yield {
            "main": client,
            "proxy": pc,
            "proxy_app": proxy_app,
            "signing_key": signing_key,
        }


# --------------------------------------------------------------------------- #
#  API-free: taOS API/auth routes 404 on the proxy origin                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestApiFree:
    async def test_taos_api_routes_absent(self, proxy_setup):
        pc = proxy_setup["proxy"]
        # /api/agents and /auth/status exist on the main app but must NOT
        # be routed on the proxy origin -> 404 (route not found), not 401.
        for path in ("/api/agents", "/auth/status", "/api/version"):
            resp = await pc.get(path)
            assert resp.status_code == 404, f"{path} should be absent, got {resp.status_code}"

    async def test_proxy_serving_routes_present(self, proxy_setup):
        proxy_app = proxy_setup["proxy_app"]
        paths = {getattr(r, "path", None) for r in proxy_app.router.routes}
        assert "/api/desktop/browser/proxy" in paths
        assert "/__taos/sw.js" in paths
        assert "/__taos/redeem" in paths

    async def test_sw_js_served_without_cookie(self, proxy_setup):
        pc = proxy_setup["proxy"]
        resp = await pc.get("/__taos/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "")


# --------------------------------------------------------------------------- #
#  Token gate on the proxy route                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestTokenGate:
    async def test_proxy_requires_cookie(self, proxy_setup):
        pc = proxy_setup["proxy"]
        resp = await pc.get(
            "/api/desktop/browser/proxy",
            params={"profile_id": "personal", "url": "http://example.com/"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "browser_proxy_unauthenticated"

    @respx.mock
    async def test_proxy_works_after_redeem(self, proxy_setup):
        pc = proxy_setup["proxy"]
        key = proxy_setup["signing_key"]
        # Resolve the admin user id from the shared auth state and mint a
        # ticket for it.
        proxy_app = proxy_setup["proxy_app"]
        record = proxy_app.state.auth.find_user("admin")
        uid = record["id"]
        _ticket, token = mint_proxy_ticket(uid, signing_key=key)

        target = "/api/desktop/browser/proxy?profile_id=personal&url=" + quote(
            "http://example.com/", safe=""
        )
        redeem = await pc.get(
            "/__taos/redeem",
            params={"ticket": token, "next": target},
            follow_redirects=False,
        )
        assert redeem.status_code == 302
        assert "taos_browser" in redeem.cookies
        assert redeem.headers["location"] == target

        # Now the cookie is on the client jar; the proxy fetch should work.
        respx.get("http://example.com/").mock(
            return_value=Response(
                200,
                content=b"<html><head></head><body>ok</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await pc.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/"},
            )
        assert resp.status_code == 200
        assert b"ok" in resp.content


# --------------------------------------------------------------------------- #
#  Redeem flow                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestRedeem:
    async def test_invalid_ticket_403(self, proxy_setup):
        pc = proxy_setup["proxy"]
        resp = await pc.get(
            "/__taos/redeem",
            params={"ticket": "not-a-real-token", "next": "/api/desktop/browser/proxy"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    async def test_expired_ticket_403(self, proxy_setup):
        pc = proxy_setup["proxy"]
        key = proxy_setup["signing_key"]
        record = proxy_setup["proxy_app"].state.auth.find_user("admin")
        # Mint with negative TTL so it's already expired.
        _ticket, token = mint_proxy_ticket(record["id"], signing_key=key, ttl=-1)
        resp = await pc.get(
            "/__taos/redeem",
            params={"ticket": token, "next": "/api/desktop/browser/proxy"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    async def test_open_redirect_rejected(self, proxy_setup):
        pc = proxy_setup["proxy"]
        key = proxy_setup["signing_key"]
        record = proxy_setup["proxy_app"].state.auth.find_user("admin")
        for bad_next in ("http://evil.com", "//evil.com", "/api/agents", "/auth/login"):
            _ticket, token = mint_proxy_ticket(record["id"], signing_key=key)
            resp = await pc.get(
                "/__taos/redeem",
                params={"ticket": token, "next": bad_next},
                follow_redirects=False,
            )
            assert resp.status_code == 403, f"next={bad_next!r} should be rejected"


# --------------------------------------------------------------------------- #
#  Main-app proxy-ticket endpoint                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestProxyTicketEndpoint:
    async def test_requires_auth(self, app):
        # A client with no taos_session cookie must be rejected by the
        # auth middleware.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            resp = await anon.post("/api/desktop/browser/proxy-ticket")
        assert resp.status_code == 401

    async def test_returns_token(self, client):
        resp = await client.post("/api/desktop/browser/proxy-ticket")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticket"]
        assert body["expires_in"] > 0

    async def test_minted_token_validates(self, client, app):
        from tinyagentos.routes.desktop_browser.proxy_ticket import validate_proxy_ticket

        resp = await client.post("/api/desktop/browser/proxy-ticket")
        token = resp.json()["ticket"]
        key = app.state.browser_proxy_signing_key
        # Validate against the shared key the endpoint used.
        ticket = validate_proxy_ticket(token, signing_key=key)
        record = app.state.auth.find_user("admin")
        assert ticket.user_id == record["id"]


# --------------------------------------------------------------------------- #
#  Main-app proxy-config probe (public — tells the frontend the proxy port)   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestProxyConfigEndpoint:
    async def test_public_no_auth_required(self, app):
        # Auth-exempt: an anonymous client must still get the port.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            resp = await anon.get("/api/desktop/browser/proxy-config")
        assert resp.status_code == 200

    async def test_reports_configured_port(self, client, app):
        app.state.browser_proxy_port = 6970
        resp = await client.get("/api/desktop/browser/proxy-config")
        assert resp.status_code == 200
        assert resp.json()["port"] == 6970

    async def test_defaults_to_zero_single_port(self, client, app):
        # No port set on state -> 0 (single-port fallback signal).
        if hasattr(app.state, "browser_proxy_port"):
            delattr(app.state, "browser_proxy_port")
        resp = await client.get("/api/desktop/browser/proxy-config")
        assert resp.status_code == 200
        assert resp.json()["port"] == 0
