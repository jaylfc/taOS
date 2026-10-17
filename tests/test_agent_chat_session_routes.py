"""Self-only chat session routes for registry agents (tsk-lwkv4t).

A registry agent holding the ``chat_session`` grant drains its own chat events
and posts replies through /api/agents/self/chat/*. The slug is derived from the
token only; a body-supplied identity must never select another agent's session.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.bridge_session import BridgeSessionRegistry
from taos_test_csrf import csrf_event_hooks

EVENTS = "/api/agents/self/chat/events"
REPLY = "/api/agents/self/chat/reply"


@pytest_asyncio.fixture
async def chat_app(app, tmp_data_dir):
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    app.state.bridge_sessions = BridgeSessionRegistry(
        trace_registry=None,
        chat_messages=None,
        chat_channels=None,
        chat_hub=None,
        archive=None,
    )
    app.state._startup_complete = True
    yield app
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


async def _agent(app, handle, *, scopes=("chat_session",), display_name=None):
    registry = app.state.agent_registry
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="taosmd",
        display_name=display_name or handle,
        origin="taos-deployed",
        handle=handle,
    )
    cid = rec["canonical_id"]
    for scope in scopes:
        await app.state.agent_grants.add_grant(cid, scope)
    return cid, mint_registry_token(cid, priv, user_id="u", framework="taosmd")


def _bare(app):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        event_hooks=csrf_event_hooks(),
    )


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestChatSessionAuth:
    async def test_no_bearer_is_refused_events(self, chat_app):
        async with _bare(chat_app) as c:
            r = await c.get(EVENTS)
        assert r.status_code in (401, 403)
        assert "text/event-stream" not in r.headers.get("content-type", "")

    async def test_no_bearer_is_refused_reply(self, chat_app):
        chat_app.state.bridge_sessions.record_reply = AsyncMock()
        async with _bare(chat_app) as c:
            r = await c.post(REPLY, json={"kind": "final", "content": "x"})
        assert r.status_code in (401, 403)
        chat_app.state.bridge_sessions.record_reply.assert_not_called()

    async def test_jwt_without_grant_gets_403(self, chat_app):
        _cid, token = await _agent(chat_app, "@no-grant", scopes=("a2a_receive",))
        chat_app.state.bridge_sessions.record_reply = AsyncMock()
        async with _bare(chat_app) as c:
            r1 = await c.get(EVENTS, headers=_hdr(token))
            r2 = await c.post(REPLY, headers=_hdr(token), json={"kind": "final"})
        assert r1.status_code == 403
        assert r2.status_code == 403
        chat_app.state.bridge_sessions.record_reply.assert_not_called()

    async def test_handle_colliding_with_local_agent_gets_403(self, chat_app):
        chat_app.state.config.agents.append({"name": "alice"})
        try:
            _cid, token = await _agent(chat_app, "@alice")
            chat_app.state.bridge_sessions.record_reply = AsyncMock()

            async def finite(slug):
                yield "event: tick\ndata: {}\n\n"

            chat_app.state.bridge_sessions.subscribe = finite
            async with _bare(chat_app) as c:
                r1 = await c.get(EVENTS, headers=_hdr(token))
                r2 = await c.post(REPLY, headers=_hdr(token), json={"kind": "final"})
        finally:
            chat_app.state.config.agents[:] = [
                a for a in chat_app.state.config.agents if a.get("name") != "alice"
            ]
        assert r1.status_code == 403
        assert r2.status_code == 403
        assert r1.json() == {"error": "handle collides with a local agent"}
        chat_app.state.bridge_sessions.record_reply.assert_not_called()

    async def test_bridge_unavailable_is_503(self, chat_app):
        _cid, token = await _agent(chat_app, "@bridge-down")
        chat_app.state.bridge_sessions = None
        async with _bare(chat_app) as c:
            r1 = await c.get(EVENTS, headers=_hdr(token))
            r2 = await c.post(REPLY, headers=_hdr(token), json={"kind": "final"})
        assert r1.status_code == 503 and r1.json() == {"error": "bridge unavailable"}
        assert r2.status_code == 503


@pytest.mark.asyncio
class TestChatSessionEvents:
    async def test_events_stream_yields_enqueued_message_for_own_slug(self, chat_app):
        _cid, token = await _agent(chat_app, "@guest-a")
        registry = chat_app.state.bridge_sessions
        real_subscribe = registry.subscribe
        subscribed = []

        async def one_frame(slug):
            subscribed.append(slug)
            async for frame in real_subscribe(slug):
                yield frame
                return

        registry.subscribe = one_frame
        await registry.enqueue_user_message("guest-a", {"id": "m1", "text": "hello"})
        await registry.enqueue_user_message("guest-b", {"id": "m2", "text": "other"})
        async with _bare(chat_app) as c:
            r = await c.get(EVENTS, headers=_hdr(token))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert subscribed == ["guest-a"]
        assert "event: user_message" in r.text
        assert "hello" in r.text
        assert "other" not in r.text

    async def test_session_slug_follows_canonical_id_not_handle(self, chat_app):
        # The router delivers to the canonical_id slug (display-name based), so a
        # handle that differs from the display name must not move the session.
        _cid, token = await _agent(
            chat_app, "@other-handle", display_name="Guest Display"
        )
        registry = chat_app.state.bridge_sessions
        real_subscribe = registry.subscribe
        subscribed = []

        async def one_frame(slug):
            subscribed.append(slug)
            async for frame in real_subscribe(slug):
                yield frame
                return

        registry.subscribe = one_frame
        await registry.enqueue_user_message("guest-display", {"id": "m1", "text": "hi"})
        async with _bare(chat_app) as c:
            r = await c.get(EVENTS, headers=_hdr(token))
        assert r.status_code == 200
        assert subscribed == ["guest-display"]
        assert "hi" in r.text


@pytest.mark.asyncio
class TestChatSessionReply:
    async def test_reply_records_under_own_slug(self, chat_app):
        _cid, token = await _agent(chat_app, "@guest-a")
        chat_app.state.bridge_sessions.record_reply = AsyncMock()
        async with _bare(chat_app) as c:
            r = await c.post(REPLY, headers=_hdr(token), json={"kind": "final", "content": "hi"})
        assert r.status_code == 202
        chat_app.state.bridge_sessions.record_reply.assert_awaited_once_with(
            "guest-a", {"kind": "final", "content": "hi"}
        )

    async def test_body_identity_is_ignored(self, chat_app):
        _a, token_a = await _agent(chat_app, "@guest-a")
        await _agent(chat_app, "@guest-b")
        mock = AsyncMock()
        chat_app.state.bridge_sessions.record_reply = mock
        body = {
            "kind": "final",
            "content": "spoof",
            "agent": "guest-b",
            "slug": "guest-b",
            "from": "@guest-b",
            "author": "guest-b",
        }
        async with _bare(chat_app) as c:
            r = await c.post(REPLY, headers=_hdr(token_a), json=body)
        assert r.status_code == 202
        slugs = [call.args[0] for call in mock.await_args_list]
        assert slugs == ["guest-a"]
        assert "guest-b" not in slugs


def test_chat_session_scope_registered_in_both_sets():
    from tinyagentos.routes.agent_auth_requests import VALID_SCOPES
    from tinyagentos.routes.agent_registry import _ALLOWED_SCOPES

    assert "chat_session" in VALID_SCOPES
    assert "chat_session" in _ALLOWED_SCOPES
