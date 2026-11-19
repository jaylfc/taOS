"""Security regressions for the userspace install/bundle routes:
- source_url install must not be SSRF-able to internal addresses
- served bundles must carry the CSP `sandbox` directive (cannot execute on the
  core origin even on a direct navigation).
"""
import io
import socket
import zipfile
from unittest.mock import patch

import httpx
import pytest

from tinyagentos.routes import userspace_apps as install_route
from tinyagentos.userspace import url_guard

WEB_MANIFEST = "id: sec\nname: Sec\nversion: 1.0.0\napp_type: web\nentry: index.html\nicon: icon.png\npermissions: []\n"


def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", WEB_MANIFEST)
        z.writestr("index.html", "<h1>sec</h1>")
        z.writestr("icon.png", "x")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_install_source_url_ssrf_blocked(client):
    for bad in (
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:6969/api/agents",
        "http://10.0.0.5/x",
    ):
        r = await client.post("/api/userspace-apps/install", json={"source_url": bad})
        assert r.status_code == 400, bad
        assert "not allowed" in r.json()["error"]


@pytest.mark.asyncio
async def test_bundle_csp_has_sandbox_directive(client):
    await client.post("/api/userspace-apps/install",
                      files={"package": ("sec.taosapp", _zip(), "application/zip")})
    r = await client.get("/api/userspace-apps/sec/bundle/index.html")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "").lower()
    assert "sandbox" in csp           # forces opaque origin on direct nav
    assert "allow-same-origin" not in csp  # never grant same-origin to a bundle


@pytest.mark.asyncio
async def test_install_source_url_fetches_original_url_through_pinned_factory(client):
    """The install route must fetch the ORIGINAL source_url through
    url_guard.userspace_fetch_client and let the factory's transport do
    the pinning (jaylfc on #4364). The route used to rewrite the URL to
    an IP literal itself, so the factory's hostname-pinning path never
    ran in production."""
    public = "93.184.216.34"

    def fake_getaddrinfo(host, port=0, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name.lower() != "pkg.test":
            raise socket.gaierror(
                socket.EAI_NONAME, f"no scripted answer for {name!r}"
            )
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                 "", (public, 0))]

    class RecordingInner(httpx.AsyncBaseTransport):
        def __init__(self):
            self.requests = []

        async def handle_async_request(self, request):
            self.requests.append(request)
            return httpx.Response(200, content=_zip(), request=request)

    inner = RecordingInner()
    real_factory = url_guard.userspace_fetch_client
    fetched_urls = []

    class SpyClient:
        """Delegates to the real factory client; records what the route
        asks it to fetch."""

        def __init__(self, real):
            self._real = real

        async def __aenter__(self):
            await self._real.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._real.__aexit__(*exc)

        async def get(self, url, **kwargs):
            fetched_urls.append(str(url))
            return await self._real.get(url, **kwargs)

    def spy_factory(**kwargs):
        return SpyClient(real_factory(transport=inner, **kwargs))

    url_guard.clear_validated_ip_cache()
    with patch("socket.getaddrinfo", fake_getaddrinfo), \
            patch.object(install_route, "userspace_fetch_client", spy_factory):
        r = await client.post(
            "/api/userspace-apps/install",
            json={"source_url": "http://pkg.test/app.taosapp"},
        )
    # The route handed the factory's client the original hostname URL --
    # not a hand-rewritten IP literal.
    assert fetched_urls == ["http://pkg.test/app.taosapp"]
    # The factory's transport pinned the connection to the validated IP,
    # preserving the original host for vhost routing.
    assert len(inner.requests) == 1
    assert inner.requests[0].url.host == public
    assert inner.requests[0].headers["host"] == "pkg.test"
    assert r.status_code == 200, r.text
