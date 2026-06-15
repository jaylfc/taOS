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
