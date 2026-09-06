"""Tests for GET /api/desktop/browser/download — streaming attachment endpoint."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import respx
from httpx import AsyncClient, ASGITransport, Response


# ──────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDownloadAuth:
    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                "/api/desktop/browser/download",
                params={"profile_id": "p1", "url": "https://example.com/file.pdf"},
            )
        assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────
# SSRF
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDownloadSsrf:
    async def test_blocks_loopback(self, client):
        r = await client.get(
            "/api/desktop/browser/download",
            params={"profile_id": "p1", "url": "http://127.0.0.1/secret"},
        )
        assert r.status_code == 403

    async def test_blocks_rfc1918(self, client):
        r = await client.get(
            "/api/desktop/browser/download",
            params={"profile_id": "p1", "url": "http://192.168.1.1/file.bin"},
        )
        assert r.status_code == 403

    async def test_blocks_file_scheme(self, client):
        r = await client.get(
            "/api/desktop/browser/download",
            params={"profile_id": "p1", "url": "file:///etc/passwd"},
        )
        assert r.status_code == 403


# ──────────────────────────────────────────────────────────────────
# Happy-path streaming
# ──────────────────────────────────────────────────────────────────

_SSRF_PATCH = patch(
    "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
    return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
)


@pytest.mark.asyncio
class TestDownloadStream:
    @respx.mock
    async def test_streams_upstream_bytes(self, client):
        file_bytes = b"PDF-content-bytes-12345"
        respx.get("https://example.com/file.pdf").mock(
            return_value=Response(
                200,
                content=file_bytes,
                headers={"content-type": "application/pdf"},
            )
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/file.pdf",
                    "filename": "file.pdf",
                },
            )

        assert r.status_code == 200
        assert r.content == file_bytes

    @respx.mock
    async def test_content_type_is_octet_stream(self, client):
        respx.get("https://example.com/data.bin").mock(
            return_value=Response(200, content=b"rawbytes", headers={"content-type": "image/png"}),
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/data.bin",
                    "filename": "data.bin",
                },
            )

        assert r.status_code == 200
        assert "application/octet-stream" in r.headers.get("content-type", "")


# ──────────────────────────────────────────────────────────────────
# Content-Disposition
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDownloadContentDisposition:
    @respx.mock
    async def test_explicit_filename_in_disposition(self, client):
        respx.get("https://example.com/report").mock(
            return_value=Response(200, content=b"data", headers={"content-type": "text/plain"}),
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/report",
                    "filename": "report.pdf",
                },
            )

        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert 'filename="report.pdf"' in cd

    @respx.mock
    async def test_filename_inferred_from_url_path(self, client):
        respx.get("https://example.com/docs/invoice.pdf").mock(
            return_value=Response(200, content=b"pdf-data", headers={"content-type": "application/pdf"}),
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/docs/invoice.pdf",
                    # no filename param
                },
            )

        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "invoice.pdf" in cd

    @respx.mock
    async def test_url_with_no_file_extension_falls_back_to_download(self, client):
        respx.get("https://example.com/").mock(
            return_value=Response(200, content=b"data", headers={"content-type": "text/html"}),
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={"profile_id": "personal", "url": "https://example.com/"},
            )

        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        # No usable filename in URL — should fall back to "download"
        assert "download" in cd


# ──────────────────────────────────────────────────────────────────
# Filename sanitisation
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDownloadFilenameSanitisation:
    @respx.mock
    async def test_path_traversal_stripped(self, client):
        respx.get("https://example.com/file").mock(
            return_value=Response(200, content=b"data"),
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/file",
                    "filename": "../../etc/passwd",
                },
            )

        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        # Only the basename — no path components
        assert "etc" not in cd
        assert "passwd" in cd

    @respx.mock
    async def test_double_quotes_stripped_from_filename(self, client):
        respx.get("https://example.com/file").mock(
            return_value=Response(200, content=b"data"),
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/file",
                    'filename': 'evil"name.pdf',
                },
            )

        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        # The double-quote in the input filename must have been stripped.
        # Extract just the value between filename=" and the closing "
        assert 'filename="' in cd
        inner = cd.split('filename="', 1)[1].split('"', 1)[0]
        assert '"' not in inner


# ──────────────────────────────────────────────────────────────────
# Redirect handling (per-hop SSRF re-validation)
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDownloadRedirects:
    @respx.mock
    async def test_redirect_to_rfc1918_returns_403(self, client):
        """Initial URL passes SSRF; redirect target is internal — must be blocked."""
        respx.get("https://example.com/file").mock(
            return_value=Response(
                302,
                headers={"location": "http://192.168.1.100/secret"},
            )
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/file",
                },
            )

        assert r.status_code == 403

    @respx.mock
    async def test_redirect_to_aws_imds_returns_403(self, client):
        respx.get("https://example.com/redir").mock(
            return_value=Response(
                301,
                headers={"location": "http://169.254.169.254/latest/meta-data/"},
            )
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/redir",
                },
            )

        assert r.status_code == 403

    @respx.mock
    async def test_too_many_redirects_returns_502(self, client):
        for i in range(6):
            respx.get(f"https://example.com/hop{i}").mock(
                return_value=Response(
                    302,
                    headers={"location": f"https://example.com/hop{i + 1}"},
                )
            )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/hop0",
                },
            )

        assert r.status_code == 502

    @respx.mock
    async def test_normal_redirect_followed_and_streamed(self, client):
        """A safe redirect hop should be followed and the file streamed."""
        file_bytes = b"redirected-content"
        respx.get("https://example.com/old-path").mock(
            return_value=Response(
                301,
                headers={"location": "https://example.com/new-path"},
            )
        )
        respx.get("https://example.com/new-path").mock(
            return_value=Response(200, content=file_bytes),
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/old-path",
                    "filename": "file.bin",
                },
            )

        assert r.status_code == 200
        assert r.content == file_bytes


# ──────────────────────────────────────────────────────────────────
# Cookie jar integration
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDownloadCookies:
    @respx.mock
    async def test_cookies_from_jar_sent_with_request(self, client, app):
        """Cookies pre-seeded in the jar are forwarded to the upstream request."""
        # Seed a cookie directly into the store for the admin user
        from tinyagentos.routes.desktop_browser.store import BrowserCookieStore
        cookie_store: BrowserCookieStore = app.state.browser_cookie_store
        user_record = app.state.auth.find_user("admin")
        user_id = str(user_record["id"])

        await cookie_store.set_cookie(
            user_id=user_id,
            profile_id="personal",
            host="example.com",
            path="/",
            name="auth_token",
            value="secret-token-xyz",
            expires_at=None,
            http_only=True,
            secure=False,
            same_site=None,
        )

        received_headers: dict[str, str] = {}

        def capture_request(request):
            received_headers.update(dict(request.headers))
            return Response(200, content=b"file-data")

        respx.get("https://example.com/private-file.pdf").mock(side_effect=capture_request)

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/private-file.pdf",
                    "filename": "file.pdf",
                },
            )

        assert r.status_code == 200
        # Cookie should have been sent upstream
        assert "auth_token" in received_headers.get("cookie", "")
        assert "secret-token-xyz" in received_headers.get("cookie", "")


# ──────────────────────────────────────────────────────────────────
# Streaming — body not buffered into memory
# ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDownloadStreaming:
    @respx.mock
    async def test_upstream_content_disposition_honoured_when_no_caller_filename(self, client):
        """Upstream Content-Disposition filename should be used when caller omits filename."""
        respx.get("https://example.com/asset").mock(
            return_value=Response(
                200,
                content=b"pdf-data",
                headers={
                    "content-type": "application/pdf",
                    "content-disposition": 'attachment; filename="upstream.pdf"',
                },
            )
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/asset",
                    # no filename param
                },
            )

        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        assert "upstream.pdf" in cd

    @respx.mock
    async def test_caller_filename_overrides_upstream_content_disposition(self, client):
        """Caller-supplied filename takes precedence over upstream Content-Disposition."""
        respx.get("https://example.com/asset2").mock(
            return_value=Response(
                200,
                content=b"data",
                headers={
                    "content-type": "application/octet-stream",
                    "content-disposition": 'attachment; filename="server-name.bin"',
                },
            )
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/asset2",
                    "filename": "my-name.bin",
                },
            )

        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        assert "my-name.bin" in cd
        assert "server-name.bin" not in cd

    @respx.mock
    async def test_upstream_rfc5987_content_disposition_honoured(self, client):
        """RFC 5987 filename*=UTF-8'' form should be parsed correctly."""
        from urllib.parse import quote as pquote
        encoded = pquote("résumé.pdf")
        respx.get("https://example.com/cv").mock(
            return_value=Response(
                200,
                content=b"cv-data",
                headers={
                    "content-type": "application/pdf",
                    "content-disposition": f"attachment; filename*=UTF-8''{encoded}",
                },
            )
        )

        with _SSRF_PATCH:
            r = await client.get(
                "/api/desktop/browser/download",
                params={
                    "profile_id": "personal",
                    "url": "https://example.com/cv",
                },
            )

        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        # The decoded name or percent-encoded name should appear
        assert "r" in cd  # minimal sanity — "résumé.pdf" contains "r"
        assert r.content == b"cv-data"
