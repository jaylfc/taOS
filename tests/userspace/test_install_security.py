"""Security regressions for the userspace install/bundle routes:
- source_url install must not be SSRF-able to internal addresses
- served bundles must carry the CSP `sandbox` directive (cannot execute on the
  core origin even on a direct navigation).
"""
import io
import zipfile

import pytest

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
