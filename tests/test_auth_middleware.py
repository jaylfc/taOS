"""Unit tests for auth_middleware allow/deny logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse
from starlette.responses import RedirectResponse

from tinyagentos.auth_middleware import (
    AuthMiddleware,
    _is_agent_canvas_path,
    _is_agent_decisions_path,
    _is_agent_task_path,
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
    routes: list | None = None,
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
    req.app.routes = routes or []
    return req


def _fake_route(path: str, methods: set[str] | None = None) -> MagicMock:
    route = MagicMock()
    route.path = path
    route.methods = methods or {"GET"}
    return route


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
            routes=[_fake_route("/api/agents/registry/grants", {"GET", "POST"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"grants": []}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
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
            routes=[_fake_route("/api/projects/{pid}/canvas/elements", {"GET", "POST"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"elements": []}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
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
            routes=[_fake_route("/api/projects/{pid}/canvas/elements/{eid}", {"PATCH", "DELETE"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_canvas_permissions_patch_requires_session(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        req = _request(
            method="PATCH",
            path="/api/projects/proj-1/canvas/permissions/agent-1",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[
                _fake_route("/api/projects/{pid}/canvas/elements", {"GET", "POST"}),
                _fake_route("/api/projects/{pid}/canvas/permissions/{aid}", {"PATCH"}),
            ],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_canvas_extra_segment_requires_session(self):
        """An extra path segment does not match any registered route, so a
        valid registry JWT returns 404 (unknown route) rather than 401."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        req = _request(
            method="GET",
            path="/api/projects/proj-1/canvas/elements/el-1/extra",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/projects/{pid}/canvas/elements", {"GET", "POST"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 404
        call_next.assert_not_awaited()


class TestIsAgentTaskChecklistPath:
    """The checklist-items list/create routes must be Bearer-reachable.

    The handlers (PR #2415) authorize agents via project_tasks_create, but
    without these allowlist entries the middleware refuses the registry JWT
    401 before any scope check runs.
    """

    def test_list_checklist_items_get_allowed(self):
        assert _is_agent_task_path("GET", "/api/projects/proj-1/tasks/tsk-1/checklist-items") is True

    def test_create_checklist_item_post_allowed(self):
        assert _is_agent_task_path("POST", "/api/projects/proj-1/tasks/tsk-1/checklist-items") is True

    def test_delete_checklist_items_not_allowed(self):
        # No DELETE handler exists; the allowlist must not widen past
        # list + create.
        assert _is_agent_task_path("DELETE", "/api/projects/proj-1/tasks/tsk-1/checklist-items") is False

    def test_single_checklist_item_path_not_allowed(self):
        # Sibling path with an extra {item_id} segment: no such route is
        # agent-reachable (archiving is store-level only, no route).
        assert _is_agent_task_path("GET", "/api/projects/proj-1/tasks/tsk-1/checklist-items/chk-1") is False
        assert _is_agent_task_path("PATCH", "/api/projects/proj-1/tasks/tsk-1/checklist-items/chk-1") is False

    def test_near_miss_sibling_not_allowed(self):
        assert _is_agent_task_path("GET", "/api/projects/proj-1/tasks/tsk-1/checklists") is False


class TestTaskChecklistAgentTokenDispatch:
    @pytest.mark.asyncio
    async def test_checklist_list_bearer_passes(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/projects/proj-1/tasks/tsk-1/checklist-items",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/projects/{pid}/tasks/{tid}/checklist-items", {"GET", "POST"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"items": []}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_checklist_create_bearer_passes(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="POST",
            path="/api/projects/proj-1/tasks/tsk-1/checklist-items",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/projects/{pid}/tasks/{tid}/checklist-items", {"GET", "POST"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_checklist_delete_requires_session(self):
        """DELETE on the checklist-items path has no matching route, so a
        valid registry JWT returns 404 (unknown route) rather than 401."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        req = _request(
            method="DELETE",
            path="/api/projects/proj-1/tasks/tsk-1/checklist-items",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/projects/{pid}/tasks/{tid}/checklist-items", {"GET", "POST"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 404
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_checklist_item_subpath_requires_session(self):
        """An extra path segment does not match any registered route, so a
        valid registry JWT returns 404 (unknown route) rather than 401."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        req = _request(
            method="GET",
            path="/api/projects/proj-1/tasks/tsk-1/checklist-items/chk-1",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/projects/{pid}/tasks/{tid}/checklist-items", {"GET", "POST"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 404
        call_next.assert_not_awaited()


class TestIsAgentDecisionsPath:
    """Prove the allowlist regexes gate the agent decision routes correctly.

    These are the tests jaylfc requested in PR #2182 -- the old regexes with
    ``$`` mid-pattern would FAIL every test below, proving the feature was
    completely inert."""

    # ── allowed paths ──────────────────────────────────────────────

    def test_post_create_allowed(self):
        assert _is_agent_decisions_path("POST", "/api/decisions") is True

    def test_post_answer_agent_allowed(self):
        """The mirror path this PR exists to build."""
        assert _is_agent_decisions_path(
            "POST", "/api/decisions/dec-abc123/answer/agent"
        ) is True

    def test_get_detail_agent_allowed(self):
        assert _is_agent_decisions_path(
            "GET", "/api/decisions/dec-abc123/agent"
        ) is True

    def test_get_list_agent_allowed(self):
        assert _is_agent_decisions_path("GET", "/api/decisions/agent") is True

    # ── refused paths ──────────────────────────────────────────────

    def test_human_get_refused(self):
        """GET /api/decisions/{id} (human session-only) must NOT match."""
        assert _is_agent_decisions_path("GET", "/api/decisions/dec-abc123") is False

    def test_human_answer_refused(self):
        """POST /api/decisions/{id}/answer (human session-only) must NOT match."""
        assert _is_agent_decisions_path(
            "POST", "/api/decisions/dec-abc123/answer"
        ) is False

    def test_nested_path_refused(self):
        """Extra path segments must not widen the pattern."""
        assert _is_agent_decisions_path(
            "POST", "/api/decisions/a/b/answer/agent"
        ) is False

    def test_wrong_method_refused(self):
        """DELETE on a decisions path must not match."""
        assert _is_agent_decisions_path(
            "DELETE", "/api/decisions/dec-abc123/agent"
        ) is False

    def test_human_list_refused(self):
        """GET /api/decisions (human session list) must NOT match."""
        assert _is_agent_decisions_path("GET", "/api/decisions") is False


class TestAgentDecisionsDispatch:
    """Middleware-layer dispatch tests: prove the agent token is admitted
    or refused at the middleware boundary, before any route handler runs."""

    @pytest.mark.asyncio
    async def test_agent_answer_mirror_path_admitted(self):
        """An agent Bearer token on the answer/agent path is passed through
        with via=registry_jwt_candidate."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="POST",
            path="/api/decisions/dec-abc123/answer/agent",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/decisions/{did}/answer/agent", {"POST"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_list_path_admitted(self):
        """GET /api/decisions/agent passes through for agent Bearer token."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/decisions/agent",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/decisions/agent", {"GET"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"items": []}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_detail_path_admitted(self):
        """GET /api/decisions/{id}/agent passes through for agent Bearer."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/decisions/dec-abc123/agent",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/decisions/{did}/agent", {"GET"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"id": "dec-abc123"}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nested_path_requires_session(self):
        """A path with extra segments does not match any registered route,
        so a valid registry JWT returns 404 (unknown route) rather than 401."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        req = _request(
            method="POST",
            path="/api/decisions/a/b/answer/agent",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/decisions/{did}/answer/agent", {"POST"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 404
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_human_answer_path_not_admitted(self):
        """POST /api/decisions/{id}/answer (human path) must NOT admit
        an agent token -- the allowlist must not widen."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        req = _request(
            method="POST",
            path="/api/decisions/dec-abc123/answer",
            headers={"authorization": "Bearer registry-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/decisions/{did}/answer", {"POST"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_path_without_bearer_requires_session(self):
        """The path is on the allowlist, but without a Bearer token the
        middleware must treat it like any other gated path (401)."""
        middleware = AuthMiddleware(app=MagicMock())
        req = _request(
            method="POST",
            path="/api/decisions/dec-abc123/answer/agent",
            headers={"accept": "application/json"},
            auth_mgr=_default_auth_mgr(),
            routes=[_fake_route("/api/decisions/{did}/answer/agent", {"POST"})],
        )
        call_next = AsyncMock()

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        call_next.assert_not_awaited()


class TestRegistryJwtRouteResolution:
    """Acceptance tests for the registry-JWT 404-vs-401 fix.

    (1) valid registry JWT + unknown route -> 404
    (2) no token + unknown route -> 401 AND no token + real route -> 401 with IDENTICAL bodies
    (3) valid registry JWT + allowlisted real route -> 2xx
    (4) valid registry JWT + KNOWN non-allowlisted route -> 401
    """

    @pytest.mark.asyncio
    async def test_valid_registry_jwt_unknown_route_returns_404(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/definitely-not-a-route",
            headers={"authorization": "Bearer valid-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/system", {"GET"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 404
        assert resp.body == b'{"error":"Not Found"}'
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_token_unknown_and_real_route_return_identical_401(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        known_route = _fake_route("/api/system", {"GET"})
        req_unknown = _request(
            path="/api/definitely-not-a-route",
            auth_mgr=auth_mgr,
            routes=[known_route],
        )
        req_known = _request(
            path="/api/system",
            auth_mgr=auth_mgr,
            routes=[known_route],
        )
        call_next = AsyncMock()

        resp_unknown = await middleware.dispatch(req_unknown, call_next)
        resp_known = await middleware.dispatch(req_known, call_next)

        assert resp_unknown.status_code == 401
        assert resp_known.status_code == 401
        assert resp_unknown.body == resp_known.body
        assert resp_unknown.body == b'{"error":"Authentication required"}'
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_registry_jwt_allowlisted_route_passes(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/agents/registry/grants",
            headers={"authorization": "Bearer valid-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/agents/registry/grants", {"GET", "POST"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"grants": []}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "registry_jwt_candidate"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_registry_jwt_known_non_allowlisted_route_returns_401(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/system",
            headers={"authorization": "Bearer valid-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/system", {"GET"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        assert resp.body == b'{"error":"Authentication required"}'
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_non_device_bearer_does_not_shadow_valid_session(self):
        """Regression for tsk-3hei4g CodeRabbit finding #3: a logged-in user
        carrying a stale non-device Bearer header on a known non-allowlisted
        route must keep their session instead of getting 401 before the
        session cookie is even consulted."""
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_session.return_value = "user-1"
        auth_mgr.get_user_by_id.return_value = {"id": "user-1", "is_admin": False}
        req = _request(
            method="GET",
            path="/api/system",
            headers={"authorization": "Bearer stale-registry-jwt"},
            cookies={"taos_session": "valid-session"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/system", {"GET"})],
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.via == "session"
        assert req.state.user_id == "user-1"
        call_next.assert_awaited_once()


class TestAnyRouteMatchesPathConverter:
    def test_path_converter_matches_slash_bearing_value(self):
        from tinyagentos.auth_middleware import _any_route_matches

        route = _fake_route("/api/secrets/{name:path}", {"GET"})
        assert _any_route_matches("GET", "/api/secrets/a2a/pi-token", [route]) is True

    def test_plain_param_still_slash_free(self):
        from tinyagentos.auth_middleware import _any_route_matches

        route = _fake_route("/api/projects/{pid}/tasks/{tid}", {"GET"})
        assert _any_route_matches("GET", "/api/projects/proj-1/tasks/tsk-1", [route]) is True
        assert _any_route_matches("GET", "/api/projects/proj-1/tasks/tsk-1/extra", [route]) is False

    @pytest.mark.asyncio
    async def test_path_converter_route_returns_401_not_404_for_valid_jwt(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.validate_local_token.return_value = False
        req = _request(
            method="GET",
            path="/api/secrets/a2a/pi-token",
            headers={"authorization": "Bearer valid-jwt"},
            auth_mgr=auth_mgr,
            routes=[_fake_route("/api/secrets/{name:path}", {"GET"})],
        )
        call_next = AsyncMock()

        with patch("tinyagentos.auth_middleware.check_agent_identity", AsyncMock(return_value="agent-1")):
            resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        assert resp.body == b'{"error":"Authentication required"}'
        call_next.assert_not_awaited()


class TestDeviceBearerPassthroughBoundary:
    """The device-bearer passthrough must match ONLY device tokens on ONLY the
    carded routes.

    Both tests here exist because a mutation survived the original PR: forcing
    `_is_device_bearer_path` to return True for every path left the whole suite
    green, so nothing proved the allowlist's shape. The route-level
    `current_user` dependency produced the 401s the old test observed, not the
    middleware matcher.
    """

    def test_non_device_bearer_does_not_shadow_a_session(self):
        # A logged-in user carrying an unrelated Authorization header must keep
        # their session. Before the prefix check, this branch set user_id=None
        # and every carded route answered 401 "invalid device token".
        from tinyagentos.auth_middleware import _is_device_bearer_path

        assert _is_device_bearer_path("GET", "/api/decisions") is True
        header = "Bearer ghp_not_a_device_token"
        from tinyagentos.device_store import DEVICE_TOKEN_PREFIX

        assert not header[7:].strip().startswith(DEVICE_TOKEN_PREFIX)

    def test_allowlist_does_not_cover_unrelated_paths(self):
        from tinyagentos.auth_middleware import _is_device_bearer_path

        # Neighbouring paths that must NOT take the passthrough.
        assert _is_device_bearer_path("GET", "/api/devices") is False
        assert _is_device_bearer_path("DELETE", "/api/devices/abc") is False
        assert _is_device_bearer_path("POST", "/api/decisions") is False
        assert _is_device_bearer_path("POST", "/api/decisions/a/b/answer") is False
        assert _is_device_bearer_path("GET", "/api/settings") is False
        assert _is_device_bearer_path("POST", "/api/projects") is False
