"""Unit tests for the /apps/{app_id}/ reverse-proxy route."""
from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from tinyagentos.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def proxy_app(tmp_data_dir):
    return create_app(data_dir=tmp_data_dir)


@pytest_asyncio.fixture
async def proxy_client(proxy_app):
    """Client with auth session and installed_apps initialised."""
    store = proxy_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await proxy_app.state.qmd_client.init()

    # Initialise installed_apps store.
    installed_apps = proxy_app.state.installed_apps
    if installed_apps._db is not None:
        await installed_apps.close()
    await installed_apps.init()

    proxy_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = proxy_app.state.auth.find_user("admin")
    token = proxy_app.state.auth.create_session(
        user_id=rec["id"] if rec else "", long_lived=True
    )
    transport = ASGITransport(app=proxy_app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": token}
    ) as c:
        yield c, proxy_app.state.installed_apps

    await installed_apps.close()
    await store.close()
    await proxy_app.state.qmd_client.close()
    await proxy_app.state.http_client.aclose()


# ---------------------------------------------------------------------------
# Redirect: /apps/{app_id} → /apps/{app_id}/
# ---------------------------------------------------------------------------

class TestTrailingSlashRedirect:
    @pytest.mark.asyncio
    async def test_no_trailing_slash_redirects(self, proxy_client):
        client, store = proxy_client
        resp = await client.get("/apps/myapp", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/apps/myapp/"


# ---------------------------------------------------------------------------
# 404 when app not installed
# ---------------------------------------------------------------------------

class TestNotInstalled:
    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_app(self, proxy_client):
        client, store = proxy_client
        resp = await client.get("/apps/does-not-exist/")
        assert resp.status_code == 404
        assert "not installed" in resp.json()["error"]


# ---------------------------------------------------------------------------
# 503 when app installed but no runtime location
# ---------------------------------------------------------------------------

class TestNoRuntimeLocation:
    @pytest.mark.asyncio
    async def test_returns_503_when_no_runtime_location(self, proxy_client):
        client, store = proxy_client
        await store.install("myapp", "1.0")
        # No update_runtime_location called → location is None.
        resp = await client.get("/apps/myapp/")
        assert resp.status_code == 503
        assert "no runtime location" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Upstream URL construction
# ---------------------------------------------------------------------------

def _make_fake_upstream(captured: dict):
    """Return a mock httpx response for upstream calls."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}

    async def _iter():
        yield b""

    mock_resp.aiter_bytes = _iter
    mock_resp.aclose = AsyncMock()
    return mock_resp


def _make_mock_client(side_effect=None, return_value=None, capture_url=None):
    """Build a mock _http_client that supports build_request / send(stream=True).

    When capture_url is a dict, stores the built URL string in capture_url["url"].
    """
    mock_client = MagicMock()

    def fake_build(method, url, **kwargs):
        """Return a lightweight object carrying the URL string."""
        req = MagicMock()
        req.url = url  # httpx passes url as a str to build_request
        if capture_url is not None:
            capture_url["url"] = str(url)
        return req

    mock_client.build_request = MagicMock(side_effect=fake_build)
    if side_effect is not None:
        mock_client.send = AsyncMock(side_effect=side_effect)
    else:
        mock_client.send = AsyncMock(return_value=return_value)
    return mock_client


class TestUpstreamURLConstruction:
    @pytest.mark.asyncio
    async def test_upstream_url_built_from_runtime_location(self, proxy_client):
        client, store = proxy_client
        await store.install("svc", "1.0")
        await store.update_runtime_location("svc", host="127.0.0.1", port=13000)

        captured = {}

        async def fake_send(req, **kwargs):
            return _make_fake_upstream(captured)

        mock_client = _make_mock_client(side_effect=fake_send, capture_url=captured)

        with patch("tinyagentos.routes.service_proxy._http_client", mock_client):
            await client.get("/apps/svc/dashboard")

        assert captured["url"] == "http://127.0.0.1:13000/dashboard"

    @pytest.mark.asyncio
    async def test_query_string_forwarded(self, proxy_client):
        client, store = proxy_client
        await store.install("svc", "1.0")
        await store.update_runtime_location("svc", host="127.0.0.1", port=13001)

        captured = {}

        async def fake_send(req, **kwargs):
            return _make_fake_upstream(captured)

        mock_client = _make_mock_client(side_effect=fake_send, capture_url=captured)

        with patch("tinyagentos.routes.service_proxy._http_client", mock_client):
            await client.get("/apps/svc/?q=hello&page=2")

        assert "q=hello" in captured["url"]
        assert "page=2" in captured["url"]


# ---------------------------------------------------------------------------
# Hop-by-hop header stripping
# ---------------------------------------------------------------------------

class TestHopByHopStripping:
    @pytest.mark.asyncio
    async def test_hop_by_hop_headers_stripped_from_request(self, proxy_client):
        client, store = proxy_client
        await store.install("svc", "1.0")
        await store.update_runtime_location("svc", host="127.0.0.1", port=13002)

        captured_headers = {}
        real_build = None

        def fake_build_request(method, url, headers=None, **kwargs):
            captured_headers.update(headers or {})
            req = MagicMock()
            req.url = url
            req.headers = headers or {}
            return req

        async def fake_send(req, **kwargs):
            return _make_fake_upstream(captured_headers)

        mock_client = MagicMock()
        mock_client.build_request = MagicMock(side_effect=fake_build_request)
        mock_client.send = AsyncMock(side_effect=fake_send)

        with patch("tinyagentos.routes.service_proxy._http_client", mock_client):
            await client.get(
                "/apps/svc/",
                headers={
                    "Connection": "keep-alive",
                    "Transfer-Encoding": "chunked",
                    "X-Custom": "value",
                },
            )

        lower = {k.lower(): v for k, v in captured_headers.items()}
        assert "connection" not in lower
        assert "transfer-encoding" not in lower
        # Non-hop header passes through.
        assert "x-custom" in lower


# ---------------------------------------------------------------------------
# Location header rewriting
# ---------------------------------------------------------------------------

class TestLocationHeaderRewrite:
    @pytest.mark.asyncio
    async def test_absolute_location_rewritten(self, proxy_client):
        client, store = proxy_client
        await store.install("svc", "1.0")
        await store.update_runtime_location("svc", host="127.0.0.1", port=13003)

        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"location": "http://127.0.0.1:13003/new-path"}

        async def _iter():
            yield b""

        mock_resp.aiter_bytes = _iter
        mock_resp.aclose = AsyncMock()
        mock_client = _make_mock_client(return_value=mock_resp)

        with patch("tinyagentos.routes.service_proxy._http_client", mock_client):
            resp = await client.get("/apps/svc/old-path", follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/apps/svc/new-path"

    @pytest.mark.asyncio
    async def test_root_relative_location_rewritten(self, proxy_client):
        # Path-absolute redirects (e.g. Gitea's "/user/login") hit the
        # controller root, not the proxied app, unless the proxy prepends
        # /apps/{app_id}. Verify we do that.
        client, store = proxy_client
        await store.install("svc", "1.0")
        await store.update_runtime_location("svc", host="127.0.0.1", port=13004)

        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"location": "/user/login"}

        async def _iter():
            yield b""

        mock_resp.aiter_bytes = _iter
        mock_resp.aclose = AsyncMock()
        mock_client = _make_mock_client(return_value=mock_resp)

        with patch("tinyagentos.routes.service_proxy._http_client", mock_client):
            resp = await client.get("/apps/svc/", follow_redirects=False)

        assert resp.headers["location"] == "/apps/svc/user/login"

    @pytest.mark.asyncio
    async def test_scheme_relative_location_passes_through(self, proxy_client):
        # //cdn.example.com/foo is scheme-relative, not proxy-scoped; leave it.
        client, store = proxy_client
        await store.install("svc", "1.0")
        await store.update_runtime_location("svc", host="127.0.0.1", port=13004)

        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"location": "//cdn.example.com/asset.js"}

        async def _iter():
            yield b""

        mock_resp.aiter_bytes = _iter
        mock_resp.aclose = AsyncMock()
        mock_client = _make_mock_client(return_value=mock_resp)

        with patch("tinyagentos.routes.service_proxy._http_client", mock_client):
            resp = await client.get("/apps/svc/", follow_redirects=False)

        assert resp.headers["location"] == "//cdn.example.com/asset.js"
