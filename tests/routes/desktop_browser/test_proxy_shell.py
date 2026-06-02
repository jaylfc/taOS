"""Tests for the /api/desktop/browser/proxy endpoint shell.

PR 2 lands the security gate (auth + SSRF check). The endpoint
returns 501 Not Implemented for valid URLs because the actual fetch +
rewriter + cookie jar logic lands in PR 3.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
class TestAuth:
    async def test_unauthenticated_request_rejected(self, app):
        # Build a fresh client with no session cookie — auth middleware must
        # reject the request before it reaches the route handler.
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/"},
            )
        assert resp.status_code == 401

    async def test_authenticated_request_passes_auth_gate(self, client):
        # `client` already carries a valid taos_session cookie — proves
        # auth gate passes. Response will be SSRF check or 501, never 401.
        resp = await client.get(
            "/api/desktop/browser/proxy",
            params={"profile_id": "personal", "url": "http://example.com/"},
        )
        assert resp.status_code != 401


@pytest.mark.asyncio
class TestSsrfGate:
    async def test_url_failing_ssrf_returns_403(self, client):
        resp = await client.get(
            "/api/desktop/browser/proxy",
            params={"profile_id": "personal", "url": "http://127.0.0.1/admin"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert "error" in body

    async def test_invalid_scheme_returns_403(self, client):
        resp = await client.get(
            "/api/desktop/browser/proxy",
            params={"profile_id": "personal", "url": "file:///etc/passwd"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert "error" in body

    async def test_403_response_does_not_leak_resolved_ip(self, client):
        """Defence against LAN enumeration via DNS-pinned hostnames.

        A remote attacker who controls a hostname can DNS-pin it to
        arbitrary internal IPs. The 403 response body must not echo the
        resolved IP back, or the attacker can read internal LAN topology.
        """
        from unittest.mock import patch

        # Resolve a controlled hostname to an internal IP we shouldn't reveal
        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("192.168.42.99", 0)),
            ],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://evil.test/"},
            )

        assert resp.status_code == 403
        body = resp.json()
        assert "192.168.42.99" not in str(body)
        assert "192.168" not in str(body)  # cover variants


@pytest.mark.asyncio
class TestParameterValidation:
    async def test_missing_url_returns_422(self, client):
        resp = await client.get(
            "/api/desktop/browser/proxy",
            params={"profile_id": "personal"},
        )
        # FastAPI returns 422 for missing required query params
        assert resp.status_code == 422

    async def test_missing_profile_returns_422(self, client):
        resp = await client.get(
            "/api/desktop/browser/proxy",
            params={"url": "http://example.com/"},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestNotImplementedStub:
    async def test_valid_request_now_attempts_fetch(self, client):
        # PR 2 returned 501 here; PR 3 makes the real fetch. With no
        # network mock the request will time out / fail fetching
        # example.com from this test environment, so we accept any
        # non-501 response — proves the gate is letting valid URLs
        # through to the fetch pipeline.
        from unittest.mock import patch
        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/"},
            )
        assert resp.status_code != 501


class TestStripPort:
    """_strip_port must drop the port without mangling IPv6 literals."""

    def test_host_with_port(self):
        from tinyagentos.routes.desktop_browser.proxy import _strip_port

        assert _strip_port("example.com:6969") == "example.com"

    def test_host_without_port(self):
        from tinyagentos.routes.desktop_browser.proxy import _strip_port

        assert _strip_port("example.com") == "example.com"

    def test_ipv6_with_port(self):
        from tinyagentos.routes.desktop_browser.proxy import _strip_port

        assert _strip_port("[2001:db8::1]:443") == "[2001:db8::1]"

    def test_ipv6_without_port_not_mangled(self):
        from tinyagentos.routes.desktop_browser.proxy import _strip_port

        # A naive rsplit(":", 1) would return "[::1" here.
        assert _strip_port("[::1]") == "[::1]"

    def test_empty(self):
        from tinyagentos.routes.desktop_browser.proxy import _strip_port

        assert _strip_port("") == ""
