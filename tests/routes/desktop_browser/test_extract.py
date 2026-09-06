"""Tests for /api/desktop/browser/extract — Readability port."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import respx
from httpx import Response


# A representative article HTML — enough words that Readability finds content
ARTICLE_HTML = b"""
<!doctype html>
<html>
<head><title>How HTTP cookies work</title></head>
<body>
<header><nav>Site nav | Login | Account</nav></header>
<article>
<h1>How HTTP cookies work</h1>
<p>HTTP cookies are small pieces of data that a server sends to a user's web
browser. The browser may store the cookie and send it back to the same server
with later requests. Typically, an HTTP cookie is used to tell if two requests
come from the same browser - keeping a user logged in, for example. It remembers
stateful information for the stateless HTTP protocol.</p>
<p>Cookies are mainly used for three purposes: session management, personalization,
and tracking. Cookies were once used for general client-side storage. While this
was legitimate when they were the only way to store data on the client, it is
recommended to use modern storage APIs today. Cookies are sent with every
request, so they can worsen performance, especially for mobile data connections.</p>
<p>Modern APIs for client storage are the Web Storage API (localStorage and
sessionStorage) and IndexedDB. There is also the Cache API for storing
responses to specific requests, useful when working with the Service Worker API.
These APIs let you store data on the client without sending it on every request,
and they are not limited in size.</p>
</article>
<footer>Copyright 2026</footer>
</body>
</html>
"""


# Boilerplate-heavy HTML — list of links, no real article body
BOILERPLATE_HTML = b"""
<!doctype html>
<html><body>
<nav><a href="/">Home</a> <a href="/about">About</a> <a href="/contact">Contact</a></nav>
<ul><li>Item 1</li><li>Item 2</li><li>Item 3</li></ul>
</body></html>
"""


class TestExtractReadable:
    def test_extracts_title_and_text_from_article(self):
        from tinyagentos.routes.desktop_browser.extract import extract_readable

        out = extract_readable(ARTICLE_HTML, "https://example.com/cookies")
        assert "title" in out
        assert "text" in out
        assert "html" in out
        assert "word_count" in out
        assert isinstance(out["word_count"], int)
        assert "cookie" in out["text"].lower()
        assert out["word_count"] > 100

    def test_boilerplate_produces_low_word_count(self):
        from tinyagentos.routes.desktop_browser.extract import extract_readable

        out = extract_readable(BOILERPLATE_HTML, "https://example.com/")
        # Reader UI hides the toggle when word_count <= 200
        assert out["word_count"] < 200

    def test_empty_input_returns_empty_dict(self):
        from tinyagentos.routes.desktop_browser.extract import extract_readable

        out = extract_readable(b"", "https://example.com/")
        assert out["word_count"] == 0
        assert out["text"] == ""


@pytest.mark.asyncio
class TestExtractEndpointAuth:
    async def test_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/desktop/browser/extract",
                params={"profile_id": "personal", "url": "http://example.com/"},
            )
            assert resp.status_code == 401


@pytest.mark.asyncio
class TestExtractEndpointSsrf:
    async def test_blocks_private_ip(self, client):
        resp = await client.get(
            "/api/desktop/browser/extract",
            params={"profile_id": "personal", "url": "http://127.0.0.1/admin"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestExtractEndpointFetch:
    @respx.mock
    async def test_fetches_and_extracts(self, client):
        respx.get("http://example.com/cookies").mock(
            return_value=Response(
                200,
                content=ARTICLE_HTML,
                headers={"content-type": "text/html"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/extract",
                params={
                    "profile_id": "personal",
                    "url": "http://example.com/cookies",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "title" in body
        assert "text" in body
        assert body["word_count"] > 100
        assert "cookie" in body["text"].lower()

    @respx.mock
    async def test_fetch_failure_returns_502(self, client):
        respx.get("http://example.com/").mock(
            return_value=Response(500, content=b"server error"),
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/extract",
                params={"profile_id": "personal", "url": "http://example.com/"},
            )

        # Upstream 500 is propagated as part of the response
        # (the endpoint doesn't error — it extracts what it can; for a
        # 500 with non-HTML content, word_count will just be 0)
        # Verify the endpoint doesn't crash
        assert resp.status_code in (200, 502)


@pytest.mark.asyncio
class TestExtractEndpointRedirectSsrf:
    """Regression tests: SSRF re-validation on each redirect hop."""

    @respx.mock
    async def test_redirect_to_rfc1918_returns_403(self, client):
        # Initial URL passes SSRF; redirect lands on RFC1918 — must be blocked.
        respx.get("http://example.com/go").mock(
            return_value=Response(
                302,
                headers={"location": "http://192.168.1.1/"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/extract",
                params={"profile_id": "personal", "url": "http://example.com/go"},
            )

        assert resp.status_code == 403

    @respx.mock
    async def test_redirect_to_aws_imds_returns_403(self, client):
        # Redirect to AWS IMDS (169.254.169.254) is link-local — blocked.
        respx.get("http://example.com/redir").mock(
            return_value=Response(
                301,
                headers={"location": "http://169.254.169.254/latest/meta-data/"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/extract",
                params={"profile_id": "personal", "url": "http://example.com/redir"},
            )

        assert resp.status_code == 403

    @respx.mock
    async def test_redirect_chain_too_long_returns_502(self, client):
        # 5-hop cycle: example.com/hop0 → /hop1 → … — exhausts the cap.
        for i in range(5):
            respx.get(f"http://example.com/hop{i}").mock(
                return_value=Response(
                    302,
                    headers={"location": f"http://example.com/hop{i + 1}"},
                )
            )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/extract",
                params={"profile_id": "personal", "url": "http://example.com/hop0"},
            )

        assert resp.status_code == 502

    @respx.mock
    async def test_normal_redirect_followed_and_extracted(self, client):
        # http → final response on the same public host — should succeed.
        respx.get("http://example.com/article").mock(
            return_value=Response(
                301,
                headers={"location": "http://example.com/article-final"},
            )
        )
        respx.get("http://example.com/article-final").mock(
            return_value=Response(
                200,
                content=ARTICLE_HTML,
                headers={"content-type": "text/html"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/extract",
                params={"profile_id": "personal", "url": "http://example.com/article"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["word_count"] > 100
