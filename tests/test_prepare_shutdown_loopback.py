"""Tests for the loopback exemption on /api/system/prepare-shutdown (task #64).

The systemd graceful-stop hook POSTs this endpoint from localhost with no
session cookie, so it was getting 401 and the in-app drain never ran. The
auth middleware now exempts the path for loopback callers only; remote callers
still hit the normal session gate.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth_middleware import AuthMiddleware


class _StubAuth:
    """Minimal AuthManager stand-in: configured, but no valid sessions."""

    def is_configured(self) -> bool:
        return True

    def validate_local_token(self, token: str) -> bool:
        return False

    def validate_session(self, token: str):
        return None

    def get_primary_user(self):
        return None

    def get_user_by_id(self, user_id: str):
        return None


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.auth = _StubAuth()
    app.add_middleware(AuthMiddleware)

    @app.post("/api/system/prepare-shutdown")
    async def _prepare_shutdown():
        return {"status": "ready"}

    @app.get("/api/agents")
    async def _agents():
        return {"agents": []}

    return app


@pytest.mark.asyncio
async def test_prepare_shutdown_allowed_from_loopback():
    """A loopback POST reaches the route without a session (drain works)."""
    app = _make_app()
    transport = ASGITransport(app=app, client=("127.0.0.1", 5555))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/system/prepare-shutdown")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_prepare_shutdown_allowed_from_ipv6_loopback():
    """::1 is loopback too and must be allowed."""
    app = _make_app()
    transport = ASGITransport(app=app, client=("::1", 5555))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/system/prepare-shutdown")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_prepare_shutdown_rejected_from_remote():
    """A non-loopback caller still hits the auth gate and gets 401."""
    app = _make_app()
    transport = ASGITransport(app=app, client=("203.0.113.5", 5555))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/system/prepare-shutdown",
            headers={"accept": "application/json"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_other_routes_still_gated_from_loopback():
    """The exemption is path-scoped: other APIs stay gated even on loopback."""
    app = _make_app()
    transport = ASGITransport(app=app, client=("127.0.0.1", 5555))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/agents", headers={"accept": "application/json"}
        )
    assert resp.status_code == 401
