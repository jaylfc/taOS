"""Tests for SecurityHeadersMiddleware (#655)."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import pytest
import respx
from httpx import ASGITransport, AsyncClient
from httpx import Response as HttpxResponse


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


_DESKTOP_DIR = Path(__file__).resolve().parents[1] / "desktop"

# The SPA HTML entry points. All are served under the strict CSP
# (`script-src 'self'`, no 'unsafe-inline'), so any inline <script> in them is
# blocked by the browser and its code silently stops running.
_SPA_HTML_FILES = ("index.html", "chat.html", "app.html")


class _ScriptTagCollector(HTMLParser):
    """Collect the attributes of every <script> start tag in a document.

    Parsing beats a regex here: a ``<script>`` tag whose attribute value contains
    a ``>`` (e.g. ``<script data-x="a>b">``) is mis-split by a naive
    ``<script\\b[^>]*>`` pattern, and a substring check for ``src=`` is satisfied
    by an unrelated attribute such as ``data-src``. HTMLParser also skips HTML
    comments and script bodies for free, so neither commented-out markup nor JS
    containing ``<`` can fool the check.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            self.scripts.append({name.lower(): value for name, value in attrs})


def _script_tags(html: str) -> list[dict[str, str | None]]:
    """Return the attributes of every <script> start tag in ``html``."""
    parser = _ScriptTagCollector()
    parser.feed(html)
    parser.close()
    return parser.scripts


class TestSpaShellCspCompatibility:
    """The SPA must not rely on inline scripts.

    Regression guard for the CSP added in #687: `script-src 'self'` (no
    'unsafe-inline', no nonce) blocks inline scripts outright. The reduce-effects
    pre-paint snippet (#58) was inline and therefore silently stopped running —
    the saved preference was only applied after React mounted, re-introducing the
    flash the snippet existed to prevent. It now lives in public/boot.js and is
    referenced by a blocking, same-origin <script src>.
    """

    @pytest.mark.parametrize("name", _SPA_HTML_FILES)
    def test_spa_html_has_no_inline_scripts(self, name):
        for attrs in _script_tags((_DESKTOP_DIR / name).read_text(encoding="utf-8")):
            assert attrs.get("src"), (
                f"desktop/{name}: inline <script> is blocked by the CSP "
                f"(script-src 'self') — move the code to an external file"
            )

    def test_prepaint_boot_script_is_external_and_present(self):
        srcs = [
            attrs["src"]
            for attrs in _script_tags((_DESKTOP_DIR / "index.html").read_text(encoding="utf-8"))
            if attrs.get("src")
        ]
        assert "/boot.js" in srcs, (
            "desktop/index.html must load the pre-paint script from /boot.js — "
            "Vite's public-dir convention; `base: '/desktop/'` rewrites it to "
            "/desktop/boot.js in the build. Found scripts: "
            f"{srcs!r}"
        )
        # The rewrite itself is pinned on the emitted shell by
        # desktop/src/__tests__/built-shell.test.ts (a real `vite build`); this
        # guard only covers the hand-written source.
        boot = _DESKTOP_DIR / "public" / "boot.js"
        assert boot.is_file(), "desktop/public/boot.js must exist (copied to the build root)"
        # Assert the behaviour, not just that the file mentions `data-perf`: a
        # boot.js that lost the preference read or the attribute write — or one
        # where both survive only inside comments — would still pass a bare
        # substring check while restoring the first-paint flash.
        boot_source = boot.read_text(encoding="utf-8")
        assert "taos-reduce-effects" in boot_source, (
            "desktop/public/boot.js must read the saved reduce-effects preference"
        )
        assert 'setAttribute("data-perf", "reduced")' in boot_source, (
            "desktop/public/boot.js must apply data-perf=reduced to the document element"
        )

    @pytest.mark.asyncio
    async def test_csp_script_src_has_no_unsafe_inline(self, client):
        resp = await client.get("/api/health")
        csp = resp.headers.get("content-security-policy", "")
        script_src = next(
            (d.strip() for d in csp.split(";") if d.strip().startswith("script-src")),
            "",
        )
        assert script_src, f"no script-src directive in CSP: {csp!r}"
        assert "'unsafe-inline'" not in script_src
        assert "'unsafe-eval'" not in script_src


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
    async def test_agent_debugger_ui_route_is_no_store(self, client):
        # A second, unrelated /agent/ route (the HTML debugger UI, not the
        # JSON debug/status handler above) so the no-store guarantee is
        # asserted against the /agent/ prefix itself rather than one
        # specific handler's behavior.
        resp = await client.get("/agent/does-not-exist/debug")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_handler_cache_control_not_clobbered(self, client):
        # /api/userspace-apps/sdk.js sets its own "no-cache" so the SDK
        # revalidates instead of being pinned; the middleware must not
        # overwrite an explicit policy. Compared against "no-store" (the
        # middleware default) rather than the handler's exact literal, so
        # this only fails if the middleware contract breaks, not if the SDK
        # handler's own policy changes.
        resp = await client.get("/api/userspace-apps/sdk.js")
        assert resp.headers.get("cache-control") != "no-store"

    @pytest.mark.asyncio
    async def test_static_assets_keep_long_cache(self, client):
        # /static/ is mounted outside /api/ and stays cacheable. Matched by
        # prefix rather than the exact TTL literal, so a future bump to the
        # static-files max-age doesn't fail a test about a different thing
        # (middleware must not clobber a static asset's cache policy).
        resp = await client.get("/static/favicon.ico")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control", "").startswith("public,")

    @pytest.mark.asyncio
    @respx.mock
    async def test_proxy_passthrough_no_upstream_cache_control_gets_no_store(self, client):
        # /api/desktop/browser/proxy forwards upstream response headers
        # verbatim (see out_headers in proxy.py); when upstream sets no
        # Cache-Control of its own, the middleware's setdefault must still
        # land, not a heuristic "assume cacheable" default.
        respx.get("http://example.com/").mock(
            return_value=HttpxResponse(
                200, content=b"hi", headers={"content-type": "text/plain"},
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
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    @respx.mock
    async def test_proxy_passthrough_preserves_upstream_cache_control(self, client):
        # An upstream page that opts into caching keeps that policy — the
        # middleware only fills in a default, it never overwrites.
        respx.get("http://example.com/").mock(
            return_value=HttpxResponse(
                200,
                content=b"hi",
                headers={"content-type": "text/plain", "cache-control": "max-age=600"},
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
        assert resp.headers.get("cache-control") == "max-age=600"


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
