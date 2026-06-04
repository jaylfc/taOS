"""Proxy POST support — form submissions (cookie consent, search, login).

The proxy was GET-only, so any form POST through it returned 405
("Method Not Allowed"). These tests cover forwarding the request method +
body to upstream and the redirect method-downgrade rules.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import respx
from httpx import Response

_SSRF_PATCH = patch(
    "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
    return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
)


@pytest.mark.asyncio
class TestProxyPost:
    @respx.mock
    async def test_forwards_post_body_to_upstream(self, client):
        route = respx.post("http://example.com/submit").mock(
            return_value=Response(200, content=b"<html><body>ok</body></html>",
                                  headers={"content-type": "text/html"}),
        )
        with _SSRF_PATCH:
            resp = await client.post(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/submit"},
                content=b"consent=all&gl=GB",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )

        assert resp.status_code == 200
        assert route.called
        sent = route.calls.last.request
        assert sent.method == "POST"
        assert sent.content == b"consent=all&gl=GB"
        assert sent.headers.get("content-type") == "application/x-www-form-urlencoded"

    @respx.mock
    async def test_post_303_redirect_downgrades_to_get(self, client):
        # 303 See Other → the next hop must be a GET with no body (HTTP spec).
        post_route = respx.post("http://example.com/submit").mock(
            return_value=Response(303, headers={"location": "http://example.com/done"}),
        )
        get_route = respx.get("http://example.com/done").mock(
            return_value=Response(200, content=b"<html><body>done</body></html>",
                                  headers={"content-type": "text/html"}),
        )
        with _SSRF_PATCH:
            resp = await client.post(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/submit"},
                content=b"consent=all",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )

        assert resp.status_code == 200
        assert post_route.called
        assert get_route.called
        # The redirect hop dropped the body and used GET.
        followed = get_route.calls.last.request
        assert followed.method == "GET"
        assert followed.content == b""

    @respx.mock
    async def test_post_307_preserves_method_and_body(self, client):
        # 307 Temporary Redirect → method + body preserved.
        first = respx.post("http://example.com/submit").mock(
            return_value=Response(307, headers={"location": "http://example.com/relay"}),
        )
        second = respx.post("http://example.com/relay").mock(
            return_value=Response(200, content=b"<html><body>relayed</body></html>",
                                  headers={"content-type": "text/html"}),
        )
        with _SSRF_PATCH:
            resp = await client.post(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/submit"},
                content=b"q=keep",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )

        assert resp.status_code == 200
        assert first.called and second.called
        relayed = second.calls.last.request
        assert relayed.method == "POST"
        assert relayed.content == b"q=keep"

    @respx.mock
    async def test_oversize_post_body_rejected(self, client):
        respx.post("http://example.com/submit").mock(
            return_value=Response(200, content=b"ok"),
        )
        big = b"x" * (10 * 1024 * 1024 + 1)  # just over the 10 MB cap
        with _SSRF_PATCH:
            resp = await client.post(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/submit"},
                content=big,
                headers={"content-type": "application/octet-stream"},
            )
        assert resp.status_code == 413

    @respx.mock
    async def test_get_still_works(self, client):
        # Regression: GET path unchanged.
        respx.get("http://example.com/page").mock(
            return_value=Response(200, content=b"<html><body>g</body></html>",
                                  headers={"content-type": "text/html"}),
        )
        with _SSRF_PATCH:
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/page"},
            )
        assert resp.status_code == 200
