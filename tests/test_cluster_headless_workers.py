"""Tests for taOS #3227: headless callers get the full worker list.

GET /api/cluster/workers is auth-exempt. The exempt middleware branch now
honours bearer <redacted> on GET requests:
- valid local token -> is_admin=True (full view)
- valid device token -> headless_full_view=True (full view)
- valid registry JWT from an active agent -> headless_full_view=True (full view)
- absent/invalid bearer token -> anonymous (stripped view, unchanged)

signing_key is stripped in all cases.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from tinyagentos import auth_middleware as am
from tinyagentos.routes.cluster import router as cluster_router


def _make_request(
    *,
    method: str = "GET",
    path: str = "/api/cluster/workers",
    headers: dict | None = None,
    cookies: dict | None = None,
) -> MagicMock:
    from types import SimpleNamespace

    req = MagicMock(spec=Request)
    req.method = method
    req.url.path = path
    req.headers = Headers(headers or {})
    req.cookies = cookies or {}
    req.client = MagicMock(host="203.0.113.5")
    req.app.state.auth = MagicMock()
    req.app.state.device_store = AsyncMock()
    req.app.routes = []
    # Use a real namespace for state so getattr(..., None) works.
    req.state = SimpleNamespace()
    return req


def _default_auth_mgr() -> MagicMock:
    mgr = MagicMock()
    mgr.is_configured.return_value = True
    mgr.validate_local_token.return_value = False
    mgr.validate_session.return_value = None
    mgr.get_primary_user.return_value = {"id": "user-1", "is_admin": True}
    mgr.get_user_by_id.return_value = None
    return mgr


def _run_dispatch(req: MagicMock, auth_mgr: MagicMock) -> MagicMock:
    """Run the middleware _dispatch with a mock call_next; return the request."""
    req.app.state.auth = auth_mgr
    seen = {}

    async def call_next(r):
        seen["state"] = {
            "is_admin": getattr(r.state, "is_admin", None),
            "headless_full_view": getattr(r.state, "headless_full_view", None),
            "via": getattr(r.state, "via", None),
        }
        return MagicMock(status_code=200)

    mw = am.AuthMiddleware.__new__(am.AuthMiddleware)
    # Use a dedicated loop rather than asyncio.get_event_loop(): when this file
    # runs after another async test module (e.g. test_llm_gateway_auth.py),
    # pytest-asyncio has already closed its loop and get_event_loop() raises
    # RuntimeError("There is no current event loop in thread 'MainThread'") on
    # 3.11+. A fresh loop makes these tests order-independent.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(mw._dispatch(req, call_next))
    finally:
        loop.close()
    out = MagicMock()
    out.state_flags = seen["state"]
    return out


class TestExemptGetBearerTokens:
    def test_anonymous_get_stays_stripped(self):
        req = _make_request()
        out = _run_dispatch(req, _default_auth_mgr())
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["via"] == "exempt"

    def test_invalid_bearer_token_stays_anonymous(self):
        # An invalid bearer token on an exempt path must NOT 401 -- the
        # path stays exempt and the caller gets the anonymous view.
        req = _make_request(headers={"authorization": "Bearer bogus-token"})
        out = _run_dispatch(req, _default_auth_mgr())
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["via"] == "exempt"

    def test_valid_local_token_gets_admin(self):
        mgr = _default_auth_mgr()
        mgr.validate_local_token.return_value = True
        req = _make_request(headers={"authorization": "Bearer local-secret"})
        out = _run_dispatch(req, mgr)
        assert out.state_flags["is_admin"] is True
        assert out.state_flags["via"] == "local_token"

    def test_local_token_without_primary_user_is_not_admin(self):
        # Pre-onboarding there is no primary user. The non-exempt flow
        # refuses admin in that case (user_id None, is_admin False); the
        # exempt-GET promotion must match it instead of handing exempt GET
        # handlers an admin with no user behind it.
        mgr = _default_auth_mgr()
        mgr.validate_local_token.return_value = True
        mgr.get_primary_user.return_value = None
        req = _make_request(headers={"authorization": "Bearer local-secret"})
        out = _run_dispatch(req, mgr)
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["via"] == "local_token"

    def test_valid_device_token_gets_full_view(self):
        mgr = _default_auth_mgr()
        device = {
            "id": "dev-1",
            "name": "pi",
            "platform": "ios",
            "scopes": "agents:read push:register",
        }
        req = _make_request(headers={"authorization": "Bearer taosdev_abc123"})
        req.app.state.device_store.get_by_token = AsyncMock(return_value=device)
        out = _run_dispatch(req, mgr)
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["headless_full_view"] is True
        assert out.state_flags["via"] == "device_bearer"

    def test_device_token_without_agents_read_stays_anonymous(self):
        # This branch bypasses require_device, so the scope check has to
        # happen here: a device holding only push:register must not read the
        # full worker list. It falls through to the anonymous view, exactly
        # as an unknown token does -- never a 401.
        mgr = _default_auth_mgr()
        device = {
            "id": "dev-2",
            "name": "phone",
            "platform": "ios",
            "scopes": "push:register",
        }
        req = _make_request(headers={"authorization": "Bearer taosdev_push_only"})
        req.app.state.device_store.get_by_token = AsyncMock(return_value=device)
        out = _run_dispatch(req, mgr)
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["via"] == "exempt"

    def test_unknown_device_token_stays_anonymous(self):
        mgr = _default_auth_mgr()
        req = _make_request(headers={"authorization": "Bearer taosdev_unknown"})
        req.app.state.device_store.get_by_token = AsyncMock(return_value=None)
        out = _run_dispatch(req, mgr)
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["via"] == "exempt"

    def test_valid_registry_jwt_gets_full_view(self):
        mgr = _default_auth_mgr()
        payload = {"sub": "agent-20260101-000000", "principal_type": "agent"}
        record = {"status": "active", "token_min_iat": 0}
        registry = AsyncMock()
        registry.get = AsyncMock(return_value=record)
        req = _make_request(headers={"authorization": "Bearer jwt-token"})
        with (
            patch(
                "tinyagentos.agent_token_auth.verify_registry_token",
                return_value=payload,
            ),
            patch.object(am, "_get_keypair", return_value=(b"priv", b"pub")),
            patch.object(am, "_get_store", return_value=registry),
            patch.object(am, "_enforce_rotation_cutoff", return_value=None),
        ):
            out = _run_dispatch(req, mgr)
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["headless_full_view"] is True
        assert out.state_flags["via"] == "registry_jwt"

    def test_inactive_agent_jwt_stays_anonymous(self):
        mgr = _default_auth_mgr()
        payload = {"sub": "agent-20260101-000000", "principal_type": "agent"}
        record = {"status": "suspended", "token_min_iat": 0}
        registry = AsyncMock()
        registry.get = AsyncMock(return_value=record)
        req = _make_request(headers={"authorization": "Bearer jwt-token"})
        with (
            patch(
                "tinyagentos.agent_token_auth.verify_registry_token",
                return_value=payload,
            ),
            patch.object(am, "_get_keypair", return_value=(b"priv", b"pub")),
            patch.object(am, "_get_store", return_value=registry),
        ):
            out = _run_dispatch(req, mgr)
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["via"] == "exempt"

    def test_bearer_ignored_on_exempt_post(self):
        # The bearer-token upgrade applies to exempt GETs only; exempt
        # POSTs (e.g. heartbeat) keep their existing behaviour.
        mgr = _default_auth_mgr()
        mgr.validate_local_token.return_value = True
        req = _make_request(
            method="POST",
            path="/api/cluster/heartbeat",
            headers={"authorization": "Bearer local-secret"},
        )
        out = _run_dispatch(req, mgr)
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["via"] == "exempt"


# --- Route-level: full vs stripped view -------------------------------------

from tinyagentos.cluster.worker_protocol import WorkerInfo


def _make_worker() -> WorkerInfo:
    return WorkerInfo(
        name="gpu-box",
        url="http://localhost:9000",
        capabilities=["chat"],
        backends=[{"name": "ollama:11434", "type": "ollama"}],
        resources=["gpu-cuda-0"],
        status="online",
        platform="linux",
        tier_id="tier-1",
        kind="worker",
        signing_key=b"\x00\x01\x02",
    )


def _route_app(*, is_admin: bool = False, headless_full_view: bool = False) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.is_admin = is_admin
        if headless_full_view:
            request.state.headless_full_view = True
        request.state.via = "test"
        return await call_next(request)

    app.include_router(cluster_router)

    cluster = MagicMock()
    cluster.get_workers.return_value = [_make_worker()]
    app.state.cluster_manager = cluster
    app.state.cluster_pairing = None
    app.state.registry = None
    return app


class TestListWorkersView:
    def test_anonymous_gets_stripped_view(self):
        app = _route_app()
        with TestClient(app) as client:
            r = client.get("/api/cluster/workers")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert set(body[0].keys()) == {"name", "status", "tier_id", "kind"}
        assert "signing_key" not in body[0]

    def test_headless_full_view_gets_full_objects(self):
        app = _route_app(headless_full_view=True)
        with TestClient(app) as client:
            r = client.get("/api/cluster/workers")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        w = body[0]
        assert w["capabilities"] == ["chat"]
        assert w["resources"] == ["gpu-cuda-0"]
        assert "signing_key" not in w

    def test_admin_gets_full_objects(self):
        app = _route_app(is_admin=True)
        with TestClient(app) as client:
            r = client.get("/api/cluster/workers")
        assert r.status_code == 200
        body = r.json()
        assert body[0]["capabilities"] == ["chat"]
        assert "signing_key" not in body[0]
