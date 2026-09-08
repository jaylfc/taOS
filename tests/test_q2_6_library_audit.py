"""RED tests for Q2-6 -- app.py and middleware audit findings.

Covers the fixes from docs/audit/library-replacement-audit-2026-09-pass2.md (Q2-6):

- Security header presence (Referrer-Policy, Permissions-Policy) on key routes
- Server header absent
- /setupfoo not matching the /setup startup-exempt prefix
- GET /manifest without ?app returns 400 with a plain body
- gui() exits 503 when the SPA bundle is missing
- X-Taos-Version coarsened for unauthenticated callers
- GZip placed inside the CSRF cookie layer
"""
from __future__ import annotations

import pytest
from fastapi.middleware.gzip import GZipMiddleware
from httpx import ASGITransport, AsyncClient

from tinyagentos.middleware.csrf import CSRFMiddleware


def _make_startonly_app(tmp_path):
    """Build an app with startup guard armed (startup incomplete)."""
    import yaml
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    app.state._startup_complete = False
    return app


class TestSecurityHeaderPresence:
    """Q2-6: Referrer-Policy and Permissions-Policy must be present on
    /auth/login, /desktop, and /api/health, alongside the existing CSP,
    X-Frame-Options, X-Content-Type-Options and X-Taos-Version headers."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/auth/login", "/desktop", "/api/health"])
    async def test_security_headers_present(self, client, path):
        resp = await client.get(path)
        assert resp.headers.get("content-security-policy", "")
        assert resp.headers.get("x-frame-options", "")
        assert resp.headers.get("x-content-type-options", "")
        assert resp.headers.get("x-taos-version", "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/auth/login", "/desktop", "/api/health"])
    async def test_referrer_policy_present(self, client, path):
        resp = await client.get(path)
        val = resp.headers.get("referrer-policy", "")
        assert val, f"Referrer-Policy missing on {path}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/auth/login", "/desktop", "/api/health"])
    async def test_permissions_policy_present(self, client, path):
        resp = await client.get(path)
        val = resp.headers.get("permissions-policy", "")
        assert val, f"Permissions-Policy missing on {path}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/auth/login", "/desktop", "/api/health"])
    async def test_server_header_absent(self, client, path):
        resp = await client.get(path)
        assert "server" not in {k.lower() for k in resp.headers}, (
            f"Server header present on {path}"
        )


class TestSetupPrefixNoCollision:
    """Q2-6: '/setup' prefix must not match '/setupfoo'.  During startup
    the guard should block /setupfoo (503), just as it would any non-exempt
    path."""

    @pytest.mark.asyncio
    async def test_setupfoo_blocked_during_startup(self, tmp_path):
        app = _make_startonly_app(tmp_path)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/setupfoo")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_setup_prefix_still_exempt_during_startup(self, tmp_path):
        """/setup/ (with trailing slash) and /setup/complete must still pass
        the startup guard -- only the prefix-collision on /setupfoo is fixed."""
        app = _make_startonly_app(tmp_path)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/setup/complete")
        assert resp.status_code != 503


class TestManifestMissingAppParam:
    """Q2-6: GET /manifest without ?app= must return 400 with a plain body,
    not the FastAPI 422 validation schema."""

    @pytest.mark.asyncio
    async def test_manifest_no_param_returns_400(self, client):
        resp = await client.get("/manifest")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_manifest_empty_param_returns_400(self, client):
        resp = await client.get("/manifest?app=")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_manifest_400_body_is_plain(self, client):
        """The body must not be the FastAPI 422 schema (detail as a list)."""
        resp = await client.get("/manifest")
        body = resp.json()
        detail = body.get("detail")
        assert not isinstance(detail, list), (
            "expected plain detail, got 422 schema"
        )


class TestGuiMissingSpaBundle:
    """Q2-6: gui() must detect a missing SPA bundle and exit 503 with a
    build hint instead of falling through."""

    def test_gui_exits_503_when_spa_missing(self, tmp_path, monkeypatch, capsys):
        import tinyagentos.app as app_mod

        monkeypatch.setattr(app_mod, "PROJECT_DIR", tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            app_mod.gui()
        assert exc_info.value.code == 503
        captured = capsys.readouterr()
        assert "build" in captured.err.lower()


class TestVersionHeaderCoarsening:
    """Q2-6: X-Taos-Version must be coarsened for unauthenticated callers."""

    @pytest.mark.asyncio
    async def test_version_coarsened_on_exempt_path(self, client):
        """/api/health is exempt from auth -- version should be coarsened."""
        import tinyagentos

        resp = await client.get("/api/health")
        assert resp.headers.get("x-taos-version", "")
        # The coarsened value must not leak the exact build version.
        assert resp.headers["x-taos-version"] != tinyagentos.__version__

    @pytest.mark.asyncio
    async def test_version_full_for_authenticated(self, client):
        """An authenticated, non-exempt API call must see the full version."""
        import tinyagentos

        resp = await client.get("/api/secrets")
        assert resp.status_code == 200
        if resp.headers.get("x-taos-version"):
            assert resp.headers["x-taos-version"] == tinyagentos.__version__


class TestGZipInsideCsrfLayer:
    """Q2-6: GZip must be added before CSRF so it is innermost to the
    cookie-setting layer (BREACH precondition)."""

    def test_gzip_added_before_csrf(self, app):
        # Starlette stores user_middleware in reverse add order (last added =
        # index 0).  GZip was added before CSRF → GZip appears at a higher
        # index, i.e. GZip is INNER (more inner than) CSRF in the stack.
        mw_classes = [m.cls for m in app.user_middleware]
        gzip_idx = mw_classes.index(GZipMiddleware)
        csrf_idx = mw_classes.index(CSRFMiddleware)
        assert gzip_idx > csrf_idx, (
            "GZip must be inside (more inner than) the CSRF cookie layer"
        )

    @pytest.mark.asyncio
    async def test_csrf_cookie_set_on_response(self, client):
        """Regression: moving GZip inside CSRF must not break cookie issuance."""
        resp = await client.get("/api/health")
        set_cookies = resp.headers.get_list("set-cookie")
        assert any("csrf_token" in sc for sc in set_cookies), "csrf_token cookie not set"
