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
class TestStreamInputValidation:
    async def test_stream_since_rejects_non_finite_cursors(self, agent_app, client):
        """`since` is a message ts, not an id. A NaN/inf cursor must not be
        forwarded to the bus -- the same silent-empty-window defect that was
        fixed on the sibling messages endpoint must not exist here."""
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        for bad in ("nan", "NaN", "inf", "-inf", "Infinity"):
            resp = await client.get(
                "/api/a2a/bus/stream",
                params={"channel": "general", "since": bad},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 400, f"{bad} was accepted as a cursor"
            assert "finite" in resp.json()["error"]

    async def test_stream_unknown_query_param_is_400(self, agent_app, client):
        """An ignored cursor param is indistinguishable from one that works.

        Measured on the live proxy before the fix on the messages endpoint:
        `since_id=2430` was silently dropped and returned 500 messages starting
        at id 1890. The stream endpoint must reject unknown params for the same
        reason."""
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        for bad in ("since_id", "after", "from_id"):
            resp = await client.get(
                "/api/a2a/bus/stream",
                params={"channel": "general", bad: "2430"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 400, f"{bad} was accepted"
            body = resp.json()
            assert bad in body["error"]
            assert "since" in body["hint"]


@pytest.mark.asyncio
class TestHeartbeatIdleUpstream:
    async def test_idle_upstream_stream_emits_ping_within_interval(self, agent_app, client):
        """An idle upstream (never yields a line) must still emit a ping
        comment so intermediaries do not reap the connection."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        from starlette.requests import Request

        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))

        upstream_done = asyncio.Event()

        async def _blocking_aiter_lines():
            await upstream_done.wait()
            yield

        upstream_resp = MagicMock()
        upstream_resp.aiter_lines = MagicMock(return_value=_blocking_aiter_lines())
        upstream_ctx = AsyncMock()
        upstream_ctx.__aenter__ = AsyncMock(return_value=upstream_resp)
        upstream_ctx.__aexit__ = AsyncMock(return_value=False)
        client_ctx = AsyncMock()
        client_ctx.stream = MagicMock(return_value=upstream_ctx)
        client_ctx.__aenter__ = AsyncMock(return_value=client_ctx)
        client_ctx.__aexit__ = AsyncMock(return_value=False)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/a2a/bus/stream",
            "root_path": "",
            "scheme": "http",
            "http_version": "1.1",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"channel=general",
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(scope, receive=receive)
        request.state.is_admin = True

        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ), patch(
            "tinyagentos.routes.a2a_bus._STREAM_HEARTBEAT_SEC",
            0.05,
        ), patch(
            "tinyagentos.routes.a2a_bus._stream_sleep",
            lambda secs: asyncio.sleep(min(secs, 0.05)),
        ):
            from tinyagentos.routes.a2a_bus import bus_stream

            resp = await bus_stream(request, channel="general")
            body_parts = []
            async for chunk in resp.body_iterator:
                body_parts.append(chunk)
                body = "".join(
                    p.decode("utf-8") if isinstance(p, bytes) else p for p in body_parts
                )
                if ": ping" in body:
                    break

        assert ": ping" in body

    async def test_line_arriving_during_heartbeat_is_not_lost(self, agent_app, client):
        """Upstream yields line A, then stalls past one heartbeat interval,
        then yields line B. The proxied stream must contain A, a ping, then
        B in order with nothing dropped."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        from starlette.requests import Request

        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))

        line_b_ready = asyncio.Event()

        async def _stall_aiter_lines():
            yield "event: message"
            yield 'data: {"id":"m1","ts":1,"from":"a","body":"A"}'
            yield ""
            await line_b_ready.wait()
            yield "event: message"
            yield 'data: {"id":"m2","ts":2,"from":"b","body":"B"}'
            yield ""

        upstream_resp = MagicMock()
        upstream_resp.aiter_lines = MagicMock(return_value=_stall_aiter_lines())
        upstream_ctx = AsyncMock()
        upstream_ctx.__aenter__ = AsyncMock(return_value=upstream_resp)
        upstream_ctx.__aexit__ = AsyncMock(return_value=False)
        client_ctx = AsyncMock()
        client_ctx.stream = MagicMock(return_value=upstream_ctx)
        client_ctx.__aenter__ = AsyncMock(return_value=client_ctx)
        client_ctx.__aexit__ = AsyncMock(return_value=False)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/a2a/bus/stream",
            "root_path": "",
            "scheme": "http",
            "http_version": "1.1",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"channel=general",
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(scope, receive=receive)
        request.state.is_admin = True

        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=client_ctx,
        ), patch(
            "tinyagentos.routes.a2a_bus._STREAM_HEARTBEAT_SEC",
            0.05,
        ), patch(
            "tinyagentos.routes.a2a_bus._stream_sleep",
            lambda secs: asyncio.sleep(min(secs, 0.05)),
        ):
            from tinyagentos.routes.a2a_bus import bus_stream

            resp = await bus_stream(request, channel="general")

            async def collect():
                body_parts = []
                async for chunk in resp.body_iterator:
                    body_parts.append(chunk)
                    body = "".join(
                        p.decode("utf-8") if isinstance(p, bytes) else p
                        for p in body_parts
                    )
                    if ": ping" in body and "B" in body:
                        break
                return "".join(
                    p.decode("utf-8") if isinstance(p, bytes) else p
                    for p in body_parts
                )

            collect_task = asyncio.create_task(collect())
            await asyncio.sleep(0.2)
            line_b_ready.set()
            body = await asyncio.wait_for(collect_task, timeout=2.0)

        body_str = body if isinstance(body, str) else body.decode()
        pos_a = body_str.index("A")
        pos_ping = body_str.index(": ping")
        pos_b = body_str.index("B")
        assert pos_a < pos_ping < pos_b
        assert "m1" in body_str
        assert "m2" in body_str


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

    async def test_messages_wildcard_channel_reads_all_threads(self, agent_app, client):
        """bus_messages treats channel=* as all-threads, matching the stream endpoint.

        This previously asserted a 400 with the rationale "all-threads is
        stream-only" -- true only because bus_messages had not implemented
        all-threads, not because reading every thread here was unwanted. The
        stream endpoint has always accepted `*` and forwarded no thread param
        (see test_stream_wildcard_channel_all_threads above), so rejecting the
        same selector on the sibling read endpoint was an inconsistency that
        pushed callers toward `all` -- which silently matched a thread literally
        named "all" and returned an empty 200 forever.
        """
        _, token = await _mint_agent(agent_app, scopes=("a2a_receive",))
        resp = await client.get(
            "/api/a2a/bus/messages",
            params={"channel": "*"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestRawBusNeverExposed:
    async def test_raw_stream_endpoint_is_not_a_proxy_route(self, client):
        # The :7900 bus must not be reachable through a taOS route. Our stream
        # route is /api/a2a/bus/stream; a raw /a2a/stream on this host must 404
        # (it is not a registered route), proving the proxy is the only path in.
        resp = await client.get("/a2a/stream", params={"thread": "general"})
        assert resp.status_code == 404
