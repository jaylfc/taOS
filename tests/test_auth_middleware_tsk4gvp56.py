from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from tinyagentos.auth_middleware import AuthMiddleware


def _request(*, method="GET", path="/api/system", headers=None, auth_mgr=None, config=None):
    req = MagicMock()
    req.method = method
    req.url.path = path
    req.headers = Headers(headers or {})
    req.cookies = {}
    req.client = MagicMock(host="203.0.113.5")
    req.app.routes = []
    req.scope = {}
    state = MagicMock()
    state.auth = auth_mgr or MagicMock()
    state.config = config
    req.app.state = state
    req.state = MagicMock()
    return req


def _default_auth_mgr():
    mgr = MagicMock()
    mgr.is_configured.return_value = True
    mgr.validate_session.return_value = None
    mgr.get_primary_user.return_value = {"id": "admin-1"}
    mgr.get_user_by_id.return_value = None
    mgr.validate_local_token.return_value = True
    return mgr


class TestBoundLocalTokenCanonicalMiddleware:
    @pytest.mark.asyncio
    async def test_mismatched_canonical_id_rejected_as_invalid_token(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.get_local_token_binding.return_value = ("helper", "A")
        config = MagicMock()
        config.agents = [{"name": "helper", "registry_canonical_id": "B"}]
        req = _request(
            headers={"authorization": "Bearer tok-old"},
            auth_mgr=auth_mgr,
            config=config,
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 401
        assert resp.body == b'{"error":"Authentication required"}'
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_matching_canonical_id_sets_agent_name(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.get_local_token_binding.return_value = ("helper", "A")
        auth_mgr.data_dir = MagicMock()
        config = MagicMock()
        config.agents = [{"name": "helper", "registry_canonical_id": "A"}]
        req = _request(
            headers={"authorization": "Bearer tok-ok"},
            auth_mgr=auth_mgr,
            config=config,
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.agent_name == "helper"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_legacy_plain_string_binding_still_sets_agent_name(self):
        middleware = AuthMiddleware(app=MagicMock())
        auth_mgr = _default_auth_mgr()
        auth_mgr.get_local_token_binding.return_value = ("helper", None)
        auth_mgr.data_dir = MagicMock()
        req = _request(
            headers={"authorization": "Bearer tok-legacy"},
            auth_mgr=auth_mgr,
            config=MagicMock(agents=[]),
        )
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        resp = await middleware.dispatch(req, call_next)

        assert resp.status_code == 200
        assert req.state.agent_name == "helper"
        call_next.assert_awaited_once()
