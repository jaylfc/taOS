"""Unit tests for auth_middleware allow/deny logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import JSONResponse
from starlette.responses import RedirectResponse

from tinyagentos.auth_middleware import (
    AuthMiddleware,
    _is_agent_canvas_path,
    _is_exempt,
    _is_loopback_client,
)


def _request(
    *,
    method: str = "GET",
    path: str = "/api/system",
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    client_host: str | None = "203.0.113.5",
    auth_mgr: MagicMock | None = None,
) -> MagicMock:
    req = MagicMock()
    req.method = method
    req.url.path = path
    req.headers = headers or {}
    req.cookies = cookies or {}
    if client_host is None:
        req.client = None
    else:
        req.client = MagicMock(host=client_host)
    req.app.state.auth = auth_mgr or MagicMock()
    return req


def _default_auth_mgr(*, configured: bool = True) -> MagicMock:
    mgr = MagicMock()
    mgr.is_configured.return_value = configured
    mgr.validate_local_token.return_value = False
    mgr.validate_session.return_value = None
    mgr.get_primary_user.return_value = None
    mgr.get_user_by_id.return_value = None
    return mgr


class TestIsExempt:
    def test_exact_exempt_paths(self):
        for path in ("/api/health", "/auth/login", "/desktop/index.html"):
            assert _is_exempt("GET", path) is True

    def test_exempt_prefixes(self):
        assert _is_exempt("GET", "/static/app.css") is True
        assert _is_exempt("GET", "/desktop/bundle.js") is True
        assert _is_exempt("GET", "/ws/chat") is True

    def test_auth_request_create_exempt(self):
        assert _is_exempt("POST", "/api/agents/auth-requests") is True

    def test_auth_request_status_poll_exempt(self):
        assert _is_exempt("GET", "/api/agents/auth-requests/req-123") is True

    def test_auth_request_approve_not_exempt(self):
        assert _is_exempt("POST", "/api/agents/auth-requests/req-123/approve") is False

    def test_auth_request_list_not_exempt(self):
        assert _is_exempt("GET", "/api/agents/auth-requests") is False

    def test_cluster_pairing_exempt(self):
        assert _is_exempt("POST", "/api/cluster/pairing/announce") is True
        assert _is_exempt("POST", "/api/cluster/pairing/claim") is True

    def test_cluster_workers_and_heartbeat_exempt(self):
        assert _is_exempt("GET", "/api/cluster/workers") is True
        assert _is_exempt("POST", "/api/cluster/workers") is True
        assert _is_exempt("POST", "/api/cluster/heartbeat") is True

    def test_protected_api_not_exempt(self):
        assert _is_exempt("GET", "/api/system") is False


class TestIsLoopbackClient:
    def test_ipv4_loopback(self):
        assert _is_loopback_client(_request(client_host="127.0.0.1")) is True

    def test_ipv6_loopback(self):
        assert _is_loopback_client(_request(client_host="::1")) is True

    def test_remote_client(self):
        assert _is_loopback_client(_request(client_host="203.0.113.5")) is False

    def test_missing_client(self):
        assert _is_loopback_client(_request(client_host=None)) is False

    def test_invalid_host(self):
        assert _is_loopback_client(_request(client_host="not-an-ip")) is False


class TestAuthMiddlewareDispatch:
    @pytest.mark.asyncio
    async def test_exempt_path_passes_without_auth(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(path="/api/health")
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "exempt"
        assert req.state.user_id is None
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unconfigured_api_returns_onboarding_401(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(path="/api/system", auth_mgr=_default_auth_mgr(configured=False))
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        assert resp.body == b'{"error":"onboarding_required","needs_onboarding":true}'
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unconfigured_html_redirects_to_setup(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            path="/",
            headers={"accept": "text/html"},
            auth_mgr=_default_auth_mgr(configured=False),
        )
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/setup"
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_session_passes(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_session.return_value = "user-1"
        auth_mgr.get_user_by_id.return_value = {"id": "user-1", "is_admin": True}
        req = _request(
            path="/api/system",
            cookies={"taos_session": "sess-token"},
            auth_mgr=auth_mgr,
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.user_id == "user-1"
        assert req.state.is_admin is True
        assert req.state.via == "session"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_session_api_returns_401(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            path="/api/system",
            headers={"accept": "application/json"},
            auth_mgr=_default_auth_mgr(),
        )
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        assert resp.body == b'{"error":"Authentication required"}'
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_session_html_redirects_to_login(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            path="/settings",
            headers={"accept": "text/html"},
            auth_mgr=_default_auth_mgr(),
        )
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login?next=/settings"
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_local_token_with_primary_user(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = True
        auth_mgr.get_primary_user.return_value = {"id": "admin-1"}
        req = _request(
            path="/api/system",
            headers={"authorization": "Bearer local-secret"},
            auth_mgr=auth_mgr,
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.user_id == "admin-1"
        assert req.state.is_admin is True
        assert req.state.via == "local_token"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_registry_feed_bearer_bypasses_session_gate(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            path="/api/agents/registry/grants",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
        )
        call_next = AsyncMock(return_value=JSONResponse({"grants": []}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_prepare_shutdown_allowed_from_loopback(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            method="POST",
            path="/api/system/prepare-shutdown",
            client_host="127.0.0.1",
            auth_mgr=_default_auth_mgr(),
        )
        call_next = AsyncMock(return_value=JSONResponse({"status": "ready"}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "loopback"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_prepare_shutdown_denied_from_remote(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            method="POST",
            path="/api/system/prepare-shutdown",
            client_host="203.0.113.5",
            headers={"accept": "application/json"},
            auth_mgr=_default_auth_mgr(),
        )
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        call_next.assert_not_awaited()


class TestIsAgentCanvasPath:
    def test_list_elements_get_allowed(self):
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/elements") is True

    def test_create_element_post_allowed(self):
        assert _is_agent_canvas_path("POST", "/api/projects/proj-1/canvas/elements") is True

    def test_delete_element_allowed(self):
        assert _is_agent_canvas_path("DELETE", "/api/projects/proj-1/canvas/elements/el-1") is True

    def test_snapshot_png_allowed(self):
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/snapshot.png") is True

    def test_snapshot_tldr_allowed(self):
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/snapshot.tldr") is True

    def test_stream_allowed(self):
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/stream") is True

    def test_update_element_patch_allowed(self):
        # canvas_write-bound agents may PATCH an element (create + update +
        # delete all live under canvas_write), so the route is on the allowlist.
        assert _is_agent_canvas_path("PATCH", "/api/projects/proj-1/canvas/elements/el-1") is True

    def test_permissions_patch_not_allowed(self):
        assert _is_agent_canvas_path("PATCH", "/api/projects/proj-1/canvas/permissions/agent-1") is False

    def test_extra_path_segment_not_allowed(self):
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/elements/el-1/extra") is False

    def test_wrong_method_not_allowed(self):
        assert _is_agent_canvas_path("POST", "/api/projects/proj-1/canvas/elements/el-1") is False

    def test_nested_element_path_not_allowed(self):
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/elements/x/y") is False

    def test_single_element_get_not_allowed(self):
        # There is no GET /elements/{id} route in the allowlist; only the
        # collection GET and the DELETE of a single element are permitted.
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/elements/el-1") is False

    def test_snapshot_dot_is_literal_not_wildcard(self):
        # The dot in snapshot.png / snapshot.tldr must be a literal, not a regex
        # wildcard: a near-miss like snapshotXpng must NOT slip through the
        # agent-token allowlist onto a session-only surface.
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/snapshotXpng") is False
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/snapshot_png") is False
        assert _is_agent_canvas_path("GET", "/api/projects/proj-1/canvas/snapshotXtldr") is False


class TestCanvasAgentTokenDispatch:
    @pytest.mark.asyncio
    async def test_canvas_list_elements_bearer_passes(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/projects/proj-1/canvas/elements",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
        )
        call_next = AsyncMock(return_value=JSONResponse({"elements": []}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_canvas_delete_element_bearer_passes(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="DELETE",
            path="/api/projects/proj-1/canvas/elements/el-1",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_canvas_permissions_patch_requires_session(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            method="PATCH",
            path="/api/projects/proj-1/canvas/permissions/agent-1",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=_default_auth_mgr(),
        )
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_canvas_extra_segment_requires_session(self):
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            method="GET",
            path="/api/projects/proj-1/canvas/elements/el-1/extra",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=_default_auth_mgr(),
        )
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        call_next.assert_not_awaited()