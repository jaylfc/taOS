"""Integration tests for the real proxy fetch pipeline (PR 3).

These exercise the orchestrator in tinyagentos/routes/desktop_browser/proxy.py
after it has been upgraded from PR 2's 501 stub. Network is mocked
via respx (already in dev deps).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import respx
from httpx import Response


@pytest.mark.asyncio
class TestProxyFetchHtml:
    @respx.mock
    async def test_fetches_html_and_rewrites_links(self, client):
        respx.get("http://example.com/page").mock(
            return_value=Response(
                200,
                content=b'<html><head></head><body><a href="/next">next</a></body></html>',
                headers={"content-type": "text/html"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/page"},
            )

        assert resp.status_code == 200
        assert b"/api/desktop/browser/proxy" in resp.content
        assert b"example.com%2Fnext" in resp.content

    @respx.mock
    async def test_injects_copilot_script(self, client):
        respx.get("http://example.com/").mock(
            return_value=Response(
                200,
                content=b'<html><head></head><body></body></html>',
                headers={"content-type": "text/html"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/"},
            )

        assert resp.status_code == 200
        assert b'<script src="/__taos/copilot.js"' in resp.content

    @respx.mock
    async def test_applies_strict_csp_to_html(self, client):
        respx.get("http://example.com/").mock(
            return_value=Response(
                200,
                content=b'<html><body>x</body></html>',
                headers={"content-type": "text/html"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/"},
            )

        assert "content-security-policy" in {k.lower() for k in resp.headers}
        csp = next(v for k, v in resp.headers.items() if k.lower() == "content-security-policy")
        assert "default-src 'self'" in csp


@pytest.mark.asyncio
class TestProxyFetchEncoding:
    @respx.mock
    async def test_utf8_page_served_without_mojibake(self, client):
        """A UTF-8 page with © / nbsp is served as UTF-8 — no `Â©` mojibake."""
        body = (
            "<html><head><meta charset=\"utf-8\"></head>"
            "<body><p>© 2026 Example Co</p></body></html>"
        ).encode("utf-8")
        respx.get("http://example.com/utf8").mock(
            return_value=Response(
                200, content=body, headers={"content-type": "text/html; charset=utf-8"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/utf8"},
            )

        assert resp.status_code == 200
        # Served as UTF-8.
        ct = next(v for k, v in resp.headers.items() if k.lower() == "content-type")
        assert "charset=utf-8" in ct.lower()
        # Real UTF-8 © bytes present; the Latin-1 mojibake signature is not.
        assert "©".encode("utf-8") in resp.content
        assert "Â©".encode("utf-8") not in resp.content
        resp.content.decode("utf-8")

    @respx.mock
    async def test_latin1_page_decoded_and_served_as_utf8(self, client):
        """Upstream ISO-8859-1 charset must not leak into our response label."""
        # Raw 0xa9 is © in Latin-1.
        body = (
            b"<html><head><meta charset=\"iso-8859-1\"></head>"
            b"<body><p>\xa9 2026 Example</p></body></html>"
        )
        respx.get("http://example.com/latin1").mock(
            return_value=Response(
                200, content=body,
                headers={"content-type": "text/html; charset=ISO-8859-1"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/latin1"},
            )

        assert resp.status_code == 200
        ct = next(v for k, v in resp.headers.items() if k.lower() == "content-type")
        assert "charset=utf-8" in ct.lower()
        assert "iso-8859-1" not in ct.lower()
        # Body decodes cleanly as UTF-8 with the correct glyph.
        assert "© 2026 Example" in resp.content.decode("utf-8")


@pytest.mark.asyncio
class TestProxyFetchNonHtml:
    @respx.mock
    async def test_passes_through_image_unchanged(self, client):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"X" * 100
        respx.get("http://example.com/img.png").mock(
            return_value=Response(
                200,
                content=png_bytes,
                headers={"content-type": "image/png"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/img.png"},
            )

        assert resp.status_code == 200
        assert resp.content == png_bytes
        assert resp.headers["content-type"] == "image/png"


@pytest.mark.asyncio
class TestProxyFetchCookies:
    @respx.mock
    async def test_persists_set_cookie_to_jar(self, client):
        respx.get("http://example.com/login").mock(
            return_value=Response(
                200,
                content=b'<html><body>logged in</body></html>',
                headers={
                    "content-type": "text/html",
                    "set-cookie": "session=abc123; Path=/",
                },
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/login"},
            )

        assert resp.status_code == 200

        # Upstream Set-Cookie headers must NOT leak into our response — the
        # cookies are stored in the server-side jar.  The CSRF middleware may
        # legitimately add its own csrf_token cookie, so we check that no
        # upstream cookie value (session=abc123) appears in any Set-Cookie
        # header rather than asserting the header is absent entirely.
        all_set_cookie = [v for k, v in resp.headers.items() if k.lower() == "set-cookie"]
        assert not any("session=abc123" in c for c in all_set_cookie), (
            "upstream Set-Cookie leaked to client response"
        )


@pytest.mark.asyncio
class TestProxyRedirects:
    @respx.mock
    async def test_redirect_target_revalidated_against_ssrf(self, client):
        # First response is a 302 to an internal IP — must be rejected by
        # SSRF guard on the redirect step
        respx.get("http://example.com/redirect").mock(
            return_value=Response(
                302,
                headers={"location": "http://127.0.0.1/admin"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/redirect"},
            )

        assert resp.status_code == 403


@pytest.mark.asyncio
class TestStaticAssets:
    async def test_copilot_js_static_serve(self, client):
        resp = await client.get("/__taos/copilot.js")

        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "").lower()
        assert b"taos-copilot" in resp.content


@pytest.mark.asyncio
class TestResponseSizeCap:
    @respx.mock
    async def test_oversized_response_returns_502(self, client):
        oversized = b"X" * (11 * 1024 * 1024)  # 11 MB > 10 MB cap
        respx.get("http://example.com/big").mock(
            return_value=Response(
                200,
                content=oversized,
                headers={"content-type": "application/octet-stream"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/big"},
            )

        assert resp.status_code == 502
        body = resp.json()
        assert "too large" in body.get("error", "").lower()


# ---------------------------------------------------------------------------
# Helper shared across TestPageChangedBroadcast tests
# ---------------------------------------------------------------------------

_SSRF_PATCH = patch(
    "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
    return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
)

_EXAMPLE_HTML = b"<html><head><title>Example</title></head><body>Hello world</body></html>"


@pytest.mark.asyncio
class TestPageChangedBroadcast:
    """The proxy emits a page-changed event to copilot_hub when tab_id is supplied."""

    @respx.mock
    async def test_broadcast_with_tab_id_and_html(self, client, app):
        """Proxy with tab_id + HTML response → push_event_to_pinned called once."""
        captured = []

        async def fake_push(*, user_id, profile_id, tab_id, event):
            captured.append(
                {"user_id": user_id, "profile_id": profile_id, "tab_id": tab_id, "event": event}
            )

        app.state.copilot_hub.push_event_to_pinned = fake_push

        respx.get("http://example.com/page").mock(
            return_value=Response(
                200,
                content=_EXAMPLE_HTML,
                headers={"content-type": "text/html"},
            )
        )

        with _SSRF_PATCH:
            await client.get(
                "/api/desktop/browser/proxy",
                params={
                    "profile_id": "personal",
                    "url": "http://example.com/page",
                    "tab_id": "tab-abc",
                },
            )

        # Allow the create_task'd broadcast to be scheduled and run.
        await asyncio.sleep(0.05)

        assert len(captured) == 1
        ev = captured[0]["event"]
        assert ev["event"] == "page-changed"
        assert ev["url"] == "http://example.com/page"
        assert "extract" in ev
        assert "title" in ev
        assert "timestamp" in ev
        assert captured[0]["tab_id"] == "tab-abc"

    @respx.mock
    async def test_no_broadcast_without_tab_id(self, client, app):
        """Proxy without tab_id → no broadcast."""
        captured = []

        async def fake_push(*, user_id, profile_id, tab_id, event):
            captured.append(event)

        app.state.copilot_hub.push_event_to_pinned = fake_push

        respx.get("http://example.com/page").mock(
            return_value=Response(
                200,
                content=_EXAMPLE_HTML,
                headers={"content-type": "text/html"},
            )
        )

        with _SSRF_PATCH:
            await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/page"},
            )

        await asyncio.sleep(0.05)
        assert captured == []

    @respx.mock
    async def test_no_broadcast_for_non_html(self, client, app):
        """Proxy with tab_id but non-HTML response (image) → no broadcast."""
        captured = []

        async def fake_push(*, user_id, profile_id, tab_id, event):
            captured.append(event)

        app.state.copilot_hub.push_event_to_pinned = fake_push

        respx.get("http://example.com/img.png").mock(
            return_value=Response(
                200,
                content=b"\x89PNG\r\n\x1a\n" + b"X" * 20,
                headers={"content-type": "image/png"},
            )
        )

        with _SSRF_PATCH:
            await client.get(
                "/api/desktop/browser/proxy",
                params={
                    "profile_id": "personal",
                    "url": "http://example.com/img.png",
                    "tab_id": "tab-abc",
                },
            )

        await asyncio.sleep(0.05)
        assert captured == []

    @respx.mock
    async def test_extract_failure_does_not_prevent_broadcast(self, client, app):
        """If extract_readable raises, the page-changed event still fires."""
        captured = []

        async def fake_push(*, user_id, profile_id, tab_id, event):
            captured.append(event)

        app.state.copilot_hub.push_event_to_pinned = fake_push

        respx.get("http://example.com/page").mock(
            return_value=Response(
                200,
                content=_EXAMPLE_HTML,
                headers={"content-type": "text/html"},
            )
        )

        with _SSRF_PATCH:
            with patch(
                "tinyagentos.routes.desktop_browser.proxy.extract_readable",
                side_effect=RuntimeError("extraction boom"),
            ):
                await client.get(
                    "/api/desktop/browser/proxy",
                    params={
                        "profile_id": "personal",
                        "url": "http://example.com/page",
                        "tab_id": "tab-abc",
                    },
                )

        await asyncio.sleep(0.05)

        assert len(captured) == 1
        assert captured[0]["event"] == "page-changed"
        assert captured[0]["extract"] == ""
        assert captured[0]["title"] == ""

    @respx.mock
    async def test_broadcast_extract_truncated_to_4000_chars(self, client, app):
        """Long page → extract is capped at 4000 chars in the event."""
        captured = []

        async def fake_push(*, user_id, profile_id, tab_id, event):
            captured.append(event)

        app.state.copilot_hub.push_event_to_pinned = fake_push

        respx.get("http://example.com/page").mock(
            return_value=Response(
                200,
                content=_EXAMPLE_HTML,
                headers={"content-type": "text/html"},
            )
        )

        long_text = "A" * 8000

        with _SSRF_PATCH:
            with patch(
                "tinyagentos.routes.desktop_browser.proxy.extract_readable",
                return_value={"title": "Big Page", "text": long_text, "html": "", "word_count": 1},
            ):
                await client.get(
                    "/api/desktop/browser/proxy",
                    params={
                        "profile_id": "personal",
                        "url": "http://example.com/page",
                        "tab_id": "tab-abc",
                    },
                )

        await asyncio.sleep(0.05)

        assert len(captured) == 1
        assert len(captured[0]["extract"]) == 4000
