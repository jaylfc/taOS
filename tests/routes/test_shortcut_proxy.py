"""Tests for shortcut dashboard reverse-proxy (Task 17 — basic HTTP forward).

Tests for Tasks 18 (auth injection) and 19 (SSE + WebSocket) follow in
subsequent commits.
"""
from __future__ import annotations

import secrets
import time
from unittest.mock import patch

import httpx
import pytest
import respx

from tinyagentos.routes.shortcut_proxy import (
    _sessions,
    _HOP_BY_HOP_PROXY,
    _filter_proxy_headers,
    _get_shortcut_from_cookie,
    _resolve_container_ip,
    proxy_dashboard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(agent_id: str, idx: int, scope: str = "dashboard") -> str:
    """Insert a valid session into _sessions and return the session_id."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "agent_id": agent_id,
        "shortcut_idx": idx,
        "scope": scope,
        "expires_at": time.monotonic() + 300,
    }
    return session_id


def _make_dashboard_shortcut(port: int = 8080, path: str = "/", auth_type: str = "none"):
    return {
        "kind": "dashboard",
        "label": "Test dashboard",
        "icon": "dashboard",
        "requires_capability": "agent.dashboard",
        "port": port,
        "path": path,
        "auth": {"type": auth_type, "token_source": None},
        "_idx": 0,
    }


# ---------------------------------------------------------------------------
# Task 17 — basic HTTP forward
# ---------------------------------------------------------------------------

class TestHopByHopConstants:
    def test_frozenset_type(self):
        assert isinstance(_HOP_BY_HOP_PROXY, frozenset)

    def test_contains_standard_headers(self):
        for h in ("connection", "keep-alive", "transfer-encoding", "upgrade", "host"):
            assert h in _HOP_BY_HOP_PROXY, f"expected '{h}' in _HOP_BY_HOP_PROXY"

    def test_proxy_authorization_included(self):
        assert "proxy-authorization" in _HOP_BY_HOP_PROXY


class TestFilterProxyHeaders:
    def test_strips_hop_by_hop(self):
        headers = {
            "content-type": "application/json",
            "transfer-encoding": "chunked",
            "connection": "keep-alive",
            "x-custom": "preserved",
        }
        filtered = _filter_proxy_headers(headers)
        assert "content-type" in filtered
        assert "x-custom" in filtered
        assert "transfer-encoding" not in filtered
        assert "connection" not in filtered

    def test_strips_taos_session_cookie(self):
        headers = {"cookie": "taos_session=abc123; other_cookie=xyz"}
        filtered = _filter_proxy_headers(headers)
        cookie_val = filtered.get("cookie", "")
        assert "taos_session" not in cookie_val
        assert "other_cookie" in cookie_val

    def test_passes_non_taos_cookies_through(self):
        headers = {"cookie": "app_session=foo; pref=bar"}
        filtered = _filter_proxy_headers(headers)
        assert "app_session" in filtered.get("cookie", "")

    def test_empty_headers_returns_empty(self):
        assert _filter_proxy_headers({}) == {}

    def test_only_taos_cookie_drops_cookie_header(self):
        headers = {"cookie": "taos_session=abc123"}
        filtered = _filter_proxy_headers(headers)
        assert "cookie" not in filtered or filtered.get("cookie", "") == ""


class TestResolveContainerIp:
    def test_returns_stored_host_if_set(self):
        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.app.state.config.agents = [
            {"id": "ag1", "name": "myagent", "host": "10.0.0.5", "framework": "openclaw"},
        ]
        ip = _resolve_container_ip(mock_request, "ag1")
        assert ip == "10.0.0.5"

    def test_returns_none_for_unknown_agent(self):
        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.app.state.config.agents = []
        ip = _resolve_container_ip(mock_request, "unknown")
        assert ip is None

    def test_looks_up_by_name_too(self):
        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.app.state.config.agents = [
            {"id": "abc123", "name": "myagent", "host": "10.0.1.2", "framework": "openclaw"},
        ]
        ip = _resolve_container_ip(mock_request, "myagent")
        assert ip == "10.0.1.2"


class TestGetShortcutFromCookie:
    def test_missing_cookie_raises_401(self):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_conn.cookies = {}
        with pytest.raises(Exception) as exc_info:
            _get_shortcut_from_cookie(mock_conn, "agent1", 0, [])
        assert exc_info.value.status_code == 401

    def test_expired_session_raises_401(self):
        from unittest.mock import MagicMock
        session_id = secrets.token_urlsafe(32)
        _sessions[session_id] = {
            "agent_id": "agent1",
            "shortcut_idx": 0,
            "scope": "dashboard",
            "expires_at": time.monotonic() - 1,
        }
        mock_conn = MagicMock()
        mock_conn.cookies = {"taos_shortcut": session_id}
        with pytest.raises(Exception) as exc_info:
            _get_shortcut_from_cookie(mock_conn, "agent1", 0, [{"kind": "dashboard"}])
        assert exc_info.value.status_code == 401

    def test_wrong_agent_raises_403(self):
        from unittest.mock import MagicMock
        session_id = _make_session("agent_A", 0)
        mock_conn = MagicMock()
        mock_conn.cookies = {"taos_shortcut": session_id}
        with pytest.raises(Exception) as exc_info:
            _get_shortcut_from_cookie(mock_conn, "agent_B", 0, [{"kind": "dashboard"}])
        assert exc_info.value.status_code == 403

    def test_wrong_idx_raises_403(self):
        from unittest.mock import MagicMock
        session_id = _make_session("agent1", 0)
        mock_conn = MagicMock()
        mock_conn.cookies = {"taos_shortcut": session_id}
        shortcuts = [{"kind": "dashboard"}, {"kind": "dashboard"}]
        with pytest.raises(Exception) as exc_info:
            _get_shortcut_from_cookie(mock_conn, "agent1", 1, shortcuts)
        assert exc_info.value.status_code == 403

    def test_valid_session_returns_shortcut(self):
        from unittest.mock import MagicMock
        shortcut = _make_dashboard_shortcut()
        session_id = _make_session("agent1", 0)
        mock_conn = MagicMock()
        mock_conn.cookies = {"taos_shortcut": session_id}
        result = _get_shortcut_from_cookie(mock_conn, "agent1", 0, [shortcut])
        assert result["kind"] == "dashboard"
        assert result["port"] == 8080

    def test_idx_out_of_range_raises_404(self):
        from unittest.mock import MagicMock
        session_id = _make_session("agent1", 5)
        mock_conn = MagicMock()
        mock_conn.cookies = {"taos_shortcut": session_id}
        with pytest.raises(Exception) as exc_info:
            _get_shortcut_from_cookie(mock_conn, "agent1", 5, [])
        assert exc_info.value.status_code == 404


class TestProxyDashboardBasic:
    @pytest.mark.asyncio
    async def test_proxies_200_response(self, app, seeded_agent_factory):
        """proxy_dashboard forwards the request and streams the upstream body."""
        shortcuts = [_make_dashboard_shortcut(port=8080)]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        agent_name = agent["id"]

        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url.query = ""
        mock_request.headers = {"accept": "text/html"}
        mock_request.app = app

        async def _empty():
            return
            yield

        mock_request.stream = _empty
        shortcut = {**shortcuts[0], "_idx": 0}

        with patch(
            "tinyagentos.routes.shortcut_proxy._resolve_container_ip",
            return_value="10.0.0.5",
        ), respx.mock(assert_all_called=False) as rsps:
            rsps.get("http://10.0.0.5:8080/").mock(
                return_value=httpx.Response(200, text="upstream ok")
            )
            resp = await proxy_dashboard(agent_name, shortcut, mock_request)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_502_when_container_unreachable(self, app, seeded_agent_factory):
        """ConnectError → 502."""
        shortcuts = [_make_dashboard_shortcut(port=9999)]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        shortcut = {**shortcuts[0], "_idx": 0}

        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url.query = ""
        mock_request.headers = {}
        mock_request.app = app

        async def _empty():
            return
            yield

        mock_request.stream = _empty

        with patch(
            "tinyagentos.routes.shortcut_proxy._resolve_container_ip",
            return_value="10.0.0.5",
        ), respx.mock(assert_all_called=False) as rsps:
            rsps.get("http://10.0.0.5:9999/").mock(
                side_effect=httpx.ConnectError("refused")
            )
            resp = await proxy_dashboard(agent["id"], shortcut, mock_request)
        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_503_when_no_container_ip(self, app, seeded_agent_factory):
        """No container IP → 503."""
        shortcuts = [_make_dashboard_shortcut()]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        shortcut = {**shortcuts[0], "_idx": 0}

        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url.query = ""
        mock_request.headers = {}
        mock_request.app = app

        async def _empty():
            return
            yield

        mock_request.stream = _empty

        with patch(
            "tinyagentos.routes.shortcut_proxy._resolve_container_ip",
            return_value=None,
        ):
            resp = await proxy_dashboard(agent["id"], shortcut, mock_request)
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_hop_by_hop_stripped_from_upstream_response(
        self, app, seeded_agent_factory
    ):
        """Hop-by-hop headers from upstream must not be forwarded to client."""
        shortcuts = [_make_dashboard_shortcut()]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        shortcut = {**shortcuts[0], "_idx": 0}

        from unittest.mock import MagicMock
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url.query = ""
        mock_request.headers = {}
        mock_request.app = app

        async def _empty():
            return
            yield

        mock_request.stream = _empty

        with patch(
            "tinyagentos.routes.shortcut_proxy._resolve_container_ip",
            return_value="10.0.0.5",
        ), respx.mock(assert_all_called=False) as rsps:
            rsps.get("http://10.0.0.5:8080/").mock(
                return_value=httpx.Response(
                    200,
                    text="body",
                    headers={
                        "transfer-encoding": "chunked",
                        "x-app-header": "keep-me",
                    },
                )
            )
            resp = await proxy_dashboard(agent["id"], shortcut, mock_request)
        assert "x-app-header" in resp.headers
        assert "transfer-encoding" not in resp.headers


class TestDashboardRouteIntegration:
    """End-to-end: GET /shortcut/dashboard/{agent}/{idx}/{path} via TestClient."""

    def test_missing_cookie_returns_401(self, test_client, seeded_agent_factory):
        shortcuts = [_make_dashboard_shortcut()]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        resp = test_client.get(
            f"/shortcut/dashboard/{agent['id']}/0/",
            follow_redirects=False,
        )
        assert resp.status_code == 401

    def test_valid_cookie_proxies_upstream(
        self, test_client, app, seeded_agent_factory, monkeypatch
    ):
        shortcuts = [_make_dashboard_shortcut(port=8080)]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        agent_id = agent["id"]

        session_id = _make_session(agent_id, 0)

        monkeypatch.setattr(
            "tinyagentos.routes.shortcut_proxy._resolve_container_ip",
            lambda req, name: "10.0.0.5",
        )

        test_client.cookies.set("taos_shortcut", session_id)
        try:
            with respx.mock(assert_all_called=False) as rsps:
                rsps.get("http://10.0.0.5:8080/").mock(
                    return_value=httpx.Response(200, text="proxied!")
                )
                resp = test_client.get(f"/shortcut/dashboard/{agent_id}/0/")
        finally:
            test_client.cookies.clear()
        assert resp.status_code == 200
        assert resp.text == "proxied!"
