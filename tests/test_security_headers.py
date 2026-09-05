"""Tests for SecurityHeadersMiddleware (#655)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def security_app(app):
    """Use the shared app fixture — SecurityHeadersMiddleware is always wired in."""
    return app


class TestSecurityHeaders:
    @pytest.mark.asyncio
    async def test_csp_header_present(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp

    @pytest.mark.asyncio
    async def test_csp_includes_websocket_connect(self, client):
        resp = await client.get("/api/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "connect-src" in csp
        assert "wss:" in csp

    @pytest.mark.asyncio
    async def test_csp_allows_weather_open_meteo_origins(self, client):
        # Regression for #1668: the built-in Weather app fetches the open-meteo
        # geocoding + forecast APIs directly, so both origins must be in
        # connect-src or default-src 'self' silently blocks every city search.
        resp = await client.get("/api/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "https://geocoding-api.open-meteo.com" in csp
        assert "https://api.open-meteo.com" in csp

    @pytest.mark.asyncio
    async def test_x_frame_options_sameorigin(self, client):
        resp = await client.get("/api/health")
        assert resp.headers.get("x-frame-options", "").upper() == "SAMEORIGIN"

    @pytest.mark.asyncio
    async def test_x_content_type_options_nosniff(self, client):
        resp = await client.get("/api/health")
        assert resp.headers.get("x-content-type-options", "").lower() == "nosniff"

    @pytest.mark.asyncio
    async def test_headers_present_on_auth_routes(self, client):
        resp = await client.get("/auth/login")
        assert resp.headers.get("x-frame-options", "").upper() == "SAMEORIGIN"
        assert resp.headers.get("x-content-type-options", "").lower() == "nosniff"


class TestProxyFrameSrc:
    def test_safe_host_allowed(self):
        from tinyagentos.middleware.security_headers import _SAFE_HOST_RE
        for h in ("192.168.6.123", "taos.local", "localhost", "a-b.example.com"):
            assert _SAFE_HOST_RE.fullmatch(h)

    def test_injection_host_rejected(self):
        from tinyagentos.middleware.security_headers import _SAFE_HOST_RE
        # A crafted Host header must not be interpolatable into the CSP.
        for h in ("evil.com; script-src *", "a b", "x'y", 'x"y', "a;b", "a,b"):
            assert not _SAFE_HOST_RE.fullmatch(h)


class TestApiNoStore:
    """Authenticated /api/* JSON must never be cacheable (tsk-piiqiw).

    Without an explicit Cache-Control a shared proxy — or the browser's
    back/forward cache on a shared machine — can hand one user's account
    data, secrets metadata or project files to the next user.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/api/secrets", "/api/agents", "/api/health"])
    async def test_api_json_is_no_store(self, client, path):
        resp = await client.get(path)
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_agent_prefix_is_no_store(self, client):
        # The /agent/ debugger surface is per-agent state, same rule. This is a
        # SUCCESSFUL response, not a 404: debugger_status answers 200 with the
        # trace counters for any agent id, so the assertion covers the
        # happy path and not just "the middleware ran on an error".
        resp = await client.get("/agent/does-not-exist/debug/status")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_handler_cache_control_not_clobbered(self, client):
        # /api/userspace-apps/sdk.js sets its own "no-cache" so the SDK
        # revalidates instead of being pinned; the middleware must not
        # overwrite an explicit policy.
        resp = await client.get("/api/userspace-apps/sdk.js")
        assert resp.headers.get("cache-control") == "no-cache"

    @pytest.mark.asyncio
    async def test_static_assets_keep_long_cache(self, client):
        # /static/ is mounted outside /api/ and stays cacheable.
        resp = await client.get("/static/favicon.ico")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "public, max-age=86400"


class TestNoStoreMiddlewareUnit:
    """Middleware-level checks against a minimal app, so the SSE and
    immutable-asset cases can be asserted without driving a live stream."""

    @staticmethod
    def _app():
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, StreamingResponse
        from starlette.routing import Route

        from tinyagentos.middleware.security_headers import SecurityHeadersMiddleware

        async def json_route(request):
            return JSONResponse({"ok": True})

        async def sse_route(request):
            async def gen():
                yield "data: hi\n\n"

            return StreamingResponse(
                gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        async def immutable_route(request):
            return JSONResponse(
                {"ok": True},
                headers={"Cache-Control": "public, max-age=86400, immutable"},
            )

        app = Starlette(
            routes=[
                Route("/api/thing", json_route),
                Route("/api/events/stream", sse_route),
                Route("/api/asset.js", immutable_route),
                Route("/other/thing", json_route),
            ]
        )
        app.add_middleware(SecurityHeadersMiddleware)
        return app

    async def _get(self, path):
        transport = ASGITransport(app=self._app())
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            return await ac.get(path)

    @pytest.mark.asyncio
    async def test_plain_api_json_gets_no_store(self):
        resp = await self._get("/api/thing")
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_sse_keeps_no_cache(self):
        resp = await self._get("/api/events/stream")
        assert resp.headers.get("cache-control") == "no-cache"

    @pytest.mark.asyncio
    async def test_immutable_asset_keeps_public_max_age(self):
        resp = await self._get("/api/asset.js")
        assert resp.headers.get("cache-control") == "public, max-age=86400, immutable"

    @pytest.mark.asyncio
    async def test_non_api_path_untouched(self):
        resp = await self._get("/other/thing")
        assert "cache-control" not in resp.headers
