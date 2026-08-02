"""Tests for the authenticated A2A SSE stream proxy (Slice S3).

Covers: an agent-JWT (bound to a project) can open the stream and receives
forwarded SSE frames relayed from the raw bus; an omitted or ``*`` channel
subscribes to ALL threads with NO ``thread`` param forwarded upstream, while a
named channel maps to ``thread``; the ``since`` cursor is honored in both
modes; the messages proxy forwards ``since`` and rejects ``*``; an
unauthenticated request is rejected 401; and the raw :7900 bus is never exposed
directly (the upstream call is made by the proxy only).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


async def _mint_agent(app, *, scopes=("a2a_receive",), project_id="prj-1"):
    """Register an active agent with *scopes*, return (canonical_id, jwt)."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    priv, _pub = app.state.agent_registry_keypair

    rec = await registry.register(
        framework="grok",
        display_name="Grok",
        origin="external-selfjoin",
        handle="@grok",
    )
    cid = rec["canonical_id"]
    await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="grok", project_id=project_id
    )
    return cid, token


@pytest_asyncio.fixture
async def agent_app(app):
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:  # noqa: SLF001
            await store.init()
    yield app
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


@pytest_asyncio.fixture
async def noauth_client(app):
    """An httpx client with NO session cookie, so requests are unauthenticated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _fake_sse_lines(lines):
    """Return an async iterator over *lines* usable as aiter_lines()."""

    async def _gen():
        for ln in lines:
            yield ln

    return _gen()


@pytest.mark.asyncio
class TestBusStreamProxy:
    async def test_agent_jwt_receives_forwarded_sse_frames(self, agent_app, client):
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        sse_body = [
            "event: message",
            'data: {"id":"m1","ts":1,"from":"a","body":"hi"}',
            "",
            'data: {"id":"m2","ts":2,"from":"b","body":"yo"}',
            "",
        ]

        upstream_resp = MagicMock()
        upstream_resp.aiter_lines = MagicMock(return_value=_fake_sse_lines(sse_body))

        upstream_ctx = AsyncMock()
        upstream_ctx.__aenter__ = AsyncMock(return_value=upstream_resp)
        upstream_ctx.__aexit__ = AsyncMock(return_value=False)

        client_ctx = AsyncMock()
        client_ctx.stream = MagicMock(return_value=upstream_ctx)
        client_ctx.__aenter__ = AsyncMock(return_value=client_ctx)
        client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/stream",
                params={"channel": "general"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert "m1" in body
        assert "m2" in body
        # The bus stream was reached via the proxy with the channel mapped to thread.
        assert client_ctx.stream.called
        call_args = client_ctx.stream.call_args
        assert call_args.args[0] == "GET"
        assert call_args.args[1].endswith("/a2a/stream")

    async def test_stream_forwards_since_cursor(self, agent_app, client):
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        upstream_resp = MagicMock()
        upstream_resp.aiter_lines = MagicMock(return_value=_fake_sse_lines([]))
        upstream_ctx = AsyncMock()
        upstream_ctx.__aenter__ = AsyncMock(return_value=upstream_resp)
        upstream_ctx.__aexit__ = AsyncMock(return_value=False)
        client_ctx = AsyncMock()
        client_ctx.stream = MagicMock(return_value=upstream_ctx)
        client_ctx.__aenter__ = AsyncMock(return_value=client_ctx)
        client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/stream",
                params={"channel": "general", "since": "1234.5"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        params = client_ctx.stream.call_args.kwargs["params"]
        assert params["thread"] == "general"
        assert float(params["since"]) == 1234.5

    def _mock_upstream(self, sse_lines=None):
        """Return a mock httpx.AsyncClient whose .stream() yields *sse_lines*."""
        sse_lines = sse_lines if sse_lines is not None else []
        upstream_resp = MagicMock()
        upstream_resp.aiter_lines = MagicMock(
            return_value=_fake_sse_lines(sse_lines)
        )
        upstream_ctx = AsyncMock()
        upstream_ctx.__aenter__ = AsyncMock(return_value=upstream_resp)
        upstream_ctx.__aexit__ = AsyncMock(return_value=False)
        client_ctx = AsyncMock()
        client_ctx.stream = MagicMock(return_value=upstream_ctx)
        client_ctx.__aenter__ = AsyncMock(return_value=client_ctx)
        client_ctx.__aexit__ = AsyncMock(return_value=False)
        return client_ctx

    async def test_stream_no_channel_all_threads(self, agent_app, client):
        """Omitting channel -> 200 SSE with NO thread param forwarded upstream."""
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        client_ctx = self._mock_upstream([
            "event: message",
            'data: {"id":"m1","ts":1,"from":"a","body":"hi"}',
            "",
        ])
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/stream",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "m1" in resp.text
        assert client_ctx.stream.called
        params = client_ctx.stream.call_args.kwargs["params"]
        assert "thread" not in params

    async def test_stream_wildcard_channel_all_threads(self, agent_app, client):
        """channel=* -> 200 SSE with NO thread param forwarded upstream."""
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        client_ctx = self._mock_upstream([])
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/stream",
                params={"channel": "*"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert client_ctx.stream.called
        params = client_ctx.stream.call_args.kwargs["params"]
        assert "thread" not in params

    async def test_stream_named_channel_forwards_thread(self, agent_app, client):
        """Named channel -> thread forwarded upstream."""
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        client_ctx = self._mock_upstream([])
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/stream",
                params={"channel": "general"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        params = client_ctx.stream.call_args.kwargs["params"]
        assert params["thread"] == "general"

    async def test_stream_since_honored_all_threads(self, agent_app, client):
        """since cursor forwarded in all-threads (no-channel) mode."""
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        client_ctx = self._mock_upstream([])
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/stream",
                params={"since": "1234.5"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        params = client_ctx.stream.call_args.kwargs["params"]
        assert "thread" not in params
        assert float(params["since"]) == 1234.5

    async def test_unauthenticated_stream_is_401(self, noauth_client):
        resp = await noauth_client.get(
            "/api/a2a/bus/stream",
            params={"channel": "general"},
        )
        assert resp.status_code == 401

    async def test_stream_forbidden_without_a2a_receive(self, agent_app, client):
        _, token = await _mint_agent(agent_app, scopes=("project_tasks",))
        resp = await client.get(
            "/api/a2a/bus/stream",
            params={"channel": "general"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestMessagesSincePassthrough:
    async def test_messages_forwards_since(self, agent_app, client):
        payload = {"messages": [{"id": "m1", "ts": 1, "from": "a", "body": "hi"}]}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = payload
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=mock_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/messages",
                params={"channel": "general", "since": "42.0"},
            )
        assert resp.status_code == 200
        call_kwargs = mock_client.get.call_args.kwargs
        assert call_kwargs["params"]["thread"] == "general"
        assert float(call_kwargs["params"]["since"]) == 42.0

    async def test_messages_rejects_wildcard_channel(self, agent_app, client):
        """bus_messages rejects channel=* (all-threads is stream-only)."""
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        resp = await client.get(
            "/api/a2a/bus/messages",
            params={"channel": "*"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestRawBusNeverExposed:
    async def test_raw_stream_endpoint_is_not_a_proxy_route(self, client):
        # The :7900 bus must not be reachable through a taOS route. Our stream
        # route is /api/a2a/bus/stream; a raw /a2a/stream on this host must 404
        # (it is not a registered route), proving the proxy is the only path in.
        resp = await client.get("/a2a/stream", params={"thread": "general"})
        assert resp.status_code == 404
