"""Tests for taOS #3227: headless callers get the full worker list.

GET /api/cluster/workers is auth-exempt. The exempt middleware branch now
honours bearer <redacted> on GET requests:
- valid local token -> is_admin=True (full view)
- valid device token -> headless_full_view=True (full view)
- valid registry JWT from an active agent -> headless_full_view=True (full view)
- registry JWT with the cluster_read scope -> cluster_read_view=True
  (full but not admin: no pairing state, host_lan_ip or hardware)
- absent/invalid bearer token -> anonymous (stripped view, unchanged)

signing_key is stripped in all cases.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
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
            "cluster_read_view": getattr(r.state, "cluster_read_view", None),
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
        grants = AsyncMock()
        grants.list_grants = AsyncMock(return_value=[{"scope": "agents:read", "expires_at": None}])
        req = _make_request(headers={"authorization": "Bearer jwt-token"})
        with (
            patch(
                "tinyagentos.agent_token_auth.verify_registry_token",
                return_value=payload,
            ),
            patch.object(am, "_get_keypair", return_value=(b"priv", b"pub")),
            patch.object(am, "_get_store", return_value=registry),
            patch.object(am, "_get_grants_store", return_value=grants),
            patch.object(am, "_enforce_rotation_cutoff", return_value=None),
        ):
            out = _run_dispatch(req, mgr)
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["headless_full_view"] is True
        assert out.state_flags["via"] == "registry_jwt"

    def test_deployed_agent_jwt_without_agents_read_stays_anonymous(self):
        # A registry JWT minted by deploy for a DEPLOYED agent carries owner
        # user_id + memory_read only (see tests/test_deploy_agent_registry_jwt.py).
        # The exempt-GET promotion requires agents:read (the same scope the
        # device-token branch enforces), so a deployed-agent JWT without it
        # must NOT get headless_full_view -- it stays anonymous, never a 401.
        mgr = _default_auth_mgr()
        payload = {
            "sub": "agent-20261029-120000",
            "principal_type": "agent",
            "iat": 1000,
        }
        record = {
            "status": "active",
            "token_min_iat": 0,
            "origin": "taos-deployed",
            "user_id": "user-1",
        }
        registry = AsyncMock()
        registry.get = AsyncMock(return_value=record)
        grants = AsyncMock()
        # Deploy mints exactly memory_read; no agents:read grant.
        grants.list_grants = AsyncMock(
            return_value=[{"scope": "memory_read", "expires_at": None}]
        )
        req = _make_request(headers={"authorization": "Bearer jwt-deployed"})
        with (
            patch(
                "tinyagentos.agent_token_auth.verify_registry_token",
                return_value=payload,
            ),
            patch.object(am, "_get_keypair", return_value=(b"priv", b"pub")),
            patch.object(am, "_get_store", return_value=registry),
            patch.object(am, "_get_grants_store", return_value=grants),
            patch.object(am, "_enforce_rotation_cutoff", return_value=None),
        ):
            out = _run_dispatch(req, mgr)
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["via"] == "exempt"

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

    def test_malformed_bearer_logs_without_traceback(self, caplog):
        # A malformed bearer (e.g. "Bearer not-a-jwt") on an exempt GET must
        # return the anonymous view and log a single WARNING line WITHOUT a
        # traceback (exc_info=None). The log must include the exception class
        # name but never the token itself.
        mgr = _default_auth_mgr()
        req = _make_request(headers={"authorization": "Bearer not-a-jwt"})
        with caplog.at_level(logging.WARNING, logger="tinyagentos.auth_middleware"):
            out = _run_dispatch(req, mgr)
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["via"] == "exempt"
        # Find the matching log record
        matching = [
            r for r in caplog.records
            if r.name == "tinyagentos.auth_middleware"
            and "exempt-GET bearer: registry JWT validation failed" in r.getMessage()
        ]
        assert len(matching) == 1, f"Expected exactly one matching log record, got {len(matching)}"
        rec = matching[0]
        assert rec.exc_info is None, f"Expected exc_info=None (no traceback), got {rec.exc_info}"
        # Exception class name should be in the message (e.g. ValueError, etc.)
        msg = rec.getMessage()
        assert "(" in msg and ")" in msg, f"Expected exception class name in message: {msg}"
        # Token must never appear in the log
        assert "not-a-jwt" not in msg, "Token must not appear in log message"

    def test_bound_agent_local_token_writes_route_record(self, tmp_path):
        # A bound-agent local token on GET /api/cluster/workers must write
        # exactly one route record to the agent token routes log with
        # "agent": <bound name> and "method": "GET". Mirrors how
        # test_agent_token_route_recorder.py reads the log.
        mgr = _default_auth_mgr()
        mgr.validate_local_token.return_value = True
        mgr.get_primary_user.return_value = {"id": "user-1", "is_admin": True}
        mgr.data_dir = tmp_path
        bound_agent_name = "test-bound-agent"
        mgr.get_local_token_agent.return_value = bound_agent_name
        
        req = _make_request(headers={"authorization": "Bearer local-secret"})
        # Add scope attribute for route template lookup
        req.scope = {"route": type("Route", (), {"path": "/api/cluster/workers"})()}
        
        out = _run_dispatch(req, mgr)
        
        assert out.state_flags["is_admin"] is True
        assert out.state_flags["via"] == "local_token"
        
        # Read the log file directly (like test_agent_token_route_recorder.py)
        log_file = tmp_path / "logs" / "agent-token-routes.jsonl"
        assert log_file.exists(), "Log file should exist"
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1, f"Expected exactly one log line, got {len(lines)}: {lines}"
        import json
        entry = json.loads(lines[0])
        assert entry["method"] == "GET", f"Expected method GET, got {entry['method']}"
        assert entry["agent"] == bound_agent_name, f"Expected agent '{bound_agent_name}', got {entry['agent']}"
        assert entry["route"] == "/api/cluster/workers", f"Expected route /api/cluster/workers, got {entry['route']}"
        assert entry["status"] == 200, f"Expected status 200, got {entry['status']}"
        assert "ts" in entry, "Missing 'ts' field"
        # Ensure token never appears in the log file
        assert "local-secret" not in log_file.read_text(), "Token must not appear in log file"


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


def _route_app(*, is_admin: bool = False, headless_full_view: bool = False,
               cluster_read_view: bool = False, worker=None,
               pairing_state: dict | None = None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.is_admin = is_admin
        if headless_full_view:
            request.state.headless_full_view = True
        if cluster_read_view:
            request.state.cluster_read_view = True
        request.state.via = "test"
        return await call_next(request)

    app.include_router(cluster_router)

    cluster = MagicMock()
    cluster.get_workers.return_value = [_make_worker() if worker is None else worker]
    app.state.cluster_manager = cluster
    if pairing_state is None:
        app.state.cluster_pairing = None
    else:
        pairing = AsyncMock()
        pairing.pairing_state = AsyncMock(return_value=pairing_state)
        app.state.cluster_pairing = pairing
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


# --- taOS #3227: the cluster_read scope tier --------------------------------
#
# jaylfc's ruling on #3227 (2026-10-01): a registry JWT carrying the new
# cluster_read grant scope gets a "full but not admin" view of the worker
# list -- capabilities, backends, resources, free_vram_mb and loaded models,
# but NOT pairing state (revoked/blocked/live_token), host_lan_ip or
# hardware. A JWT without the scope gets the anonymous view. The middleware
# surfaces the scope as request.state.cluster_read_view; the route tiers on
# it. The caller is never promoted to admin and never gets the unrestricted
# headless_full_view on the strength of cluster_read alone.


def _jwt_dispatch(grants_list, *, record=None, payload=None):
    """Run the exempt-GET dispatch for a registry JWT with the given grants."""
    mgr = _default_auth_mgr()
    payload = payload or {"sub": "agent-20260101-000000", "principal_type": "agent"}
    record = record or {"status": "active", "token_min_iat": 0}
    registry = AsyncMock()
    registry.get = AsyncMock(return_value=record)
    grants = AsyncMock()
    grants.list_grants = AsyncMock(return_value=grants_list)
    req = _make_request(headers={"authorization": "Bearer <redacted>"})
    with (
        patch(
            "tinyagentos.agent_token_auth.verify_registry_token",
            return_value=payload,
        ),
        patch.object(am, "_get_keypair", return_value=(b"priv", b"pub")),
        patch.object(am, "_get_store", return_value=registry),
        patch.object(am, "_get_grants_store", return_value=grants),
        patch.object(am, "_enforce_rotation_cutoff", return_value=None),
    ):
        return _run_dispatch(req, mgr)


class TestExemptGetClusterReadScope:
    def test_jwt_with_cluster_read_gets_cluster_read_view(self):
        out = _jwt_dispatch([{"scope": "cluster_read", "expires_at": None}])
        assert out.state_flags["is_admin"] is False
        # cluster_read alone must NOT promote to the unrestricted full view.
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["cluster_read_view"] is True
        assert out.state_flags["via"] == "registry_jwt"

    def test_jwt_without_cluster_read_stays_anonymous(self):
        # Acceptance (#3227): a JWT without the scope gets the anonymous
        # view -- here an agent holding only an unrelated scope.
        out = _jwt_dispatch([{"scope": "memory_read", "expires_at": None}])
        assert out.state_flags["is_admin"] is False
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["cluster_read_view"] is None
        assert out.state_flags["via"] == "exempt"

    def test_jwt_with_expired_cluster_read_stays_anonymous(self):
        out = _jwt_dispatch(
            [{"scope": "cluster_read", "expires_at": "2020-01-01T00:00:00+00:00"}]
        )
        assert out.state_flags["cluster_read_view"] is None
        assert out.state_flags["headless_full_view"] is None
        assert out.state_flags["via"] == "exempt"

    def test_jwt_with_cluster_read_but_inactive_agent_stays_anonymous(self):
        out = _jwt_dispatch(
            [{"scope": "cluster_read", "expires_at": None}],
            record={"status": "suspended", "token_min_iat": 0},
        )
        assert out.state_flags["cluster_read_view"] is None
        assert out.state_flags["via"] == "exempt"


def _make_rich_worker() -> WorkerInfo:
    return WorkerInfo(
        name="gpu-box",
        url="http://localhost:9000",
        hardware={"gpu": "RTX 4090", "gpu_vram_mb": 24576},
        capabilities=["chat"],
        backends=[
            {
                "name": "ollama:11434",
                "type": "ollama",
                "loaded_models": ["llama3.1"],
            }
        ],
        models=["llama3.1"],
        resources=["gpu-cuda-0"],
        status="online",
        platform="linux",
        tier_id="tier-1",
        kind="worker",
        signing_key=b"\x00\x01\x02",
        host_lan_ip="192.168.1.50",
        free_vram_mb=8192,
    )


class TestListWorkersClusterReadView:
    def test_cluster_read_view_is_full_but_not_admin(self):
        # Pairing state exists for this worker; the cluster_read tier must
        # not surface it (that merge is admin-only).
        app = _route_app(
            cluster_read_view=True,
            worker=_make_rich_worker(),
            pairing_state={"revoked": True, "blocked": False},
        )
        with TestClient(app) as client:
            r = client.get("/api/cluster/workers")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        w = body[0]
        # The full operational picture is visible...
        assert w["capabilities"] == ["chat"]
        assert w["resources"] == ["gpu-cuda-0"]
        assert w["free_vram_mb"] == 8192
        assert w["models"] == ["llama3.1"]
        assert w["backends"][0]["loaded_models"] == ["llama3.1"]
        # ...but nothing admin-only leaks.
        assert "revoked" not in w
        assert "blocked" not in w
        assert "live_token" not in w
        assert "host_lan_ip" not in w
        assert "hardware" not in w
        assert "signing_key" not in w

    def test_cluster_read_view_without_pairing_store_adds_no_pairing_keys(self):
        app = _route_app(cluster_read_view=True, worker=_make_rich_worker())
        with TestClient(app) as client:
            r = client.get("/api/cluster/workers")
        assert r.status_code == 200
        w = r.json()[0]
        assert "revoked" not in w
        assert "blocked" not in w
        assert "live_token" not in w
        assert w["free_vram_mb"] == 8192
