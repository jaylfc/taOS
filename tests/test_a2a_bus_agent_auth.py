"""Agent-token authentication for the read-only A2A bus proxy.

An agent reads /api/a2a/bus/* with its OWN Ed25519 registry JWT (scope
a2a_receive), never the owner session/password.  These tests cover the full
edge-case matrix Jay flagged plus the admin/local-token regressions and the
skeleton-key guard (the agent token must not authenticate any other route).
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import (
    load_or_create_signing_keypair,
    mint_registry_token,
)
from taos_test_csrf import csrf_event_hooks

_BUS_PATCH = "tinyagentos.routes.a2a_bus.httpx.AsyncClient"


class TestGrantUnexpired:
    """Grant-expiry must be parsed, not string-compared: a naive or oddly
    formatted timestamp must never read an expired grant as live (access after
    revocation). Fail closed on anything unparseable."""

    def _now(self):
        return datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_never_expires(self):
        from tinyagentos.agent_token_auth import _grant_unexpired
        assert _grant_unexpired(None, self._now()) is True

    def test_future_aware_is_live(self):
        from tinyagentos.agent_token_auth import _grant_unexpired
        now = self._now()
        assert _grant_unexpired((now + timedelta(hours=1)).isoformat(), now) is True

    def test_past_aware_is_expired(self):
        from tinyagentos.agent_token_auth import _grant_unexpired
        now = self._now()
        assert _grant_unexpired((now - timedelta(hours=1)).isoformat(), now) is False

    def test_naive_past_assumed_utc_is_expired(self):
        from tinyagentos.agent_token_auth import _grant_unexpired
        now = self._now()
        naive_past = (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        assert _grant_unexpired(naive_past, now) is False

    def test_unparseable_fails_closed(self):
        from tinyagentos.agent_token_auth import _grant_unexpired
        assert _grant_unexpired("not-a-timestamp", self._now()) is False


def _mock_bus_client(json_payload: dict):
    """A mock httpx.AsyncClient whose async context manager yields a client whose
    .get().json() returns *json_payload* (so no real network call is made)."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = json_payload

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest_asyncio.fixture
async def bus_client(app, tmp_data_dir):
    """Admin client with the registry + grants stores initialised.

    Exposes ._app so tests can register agents / mint tokens directly against the
    stores and drive bare (cookieless) requests with a Bearer header.
    """
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        c._app = app
        c._test_admin_uid = uid
        yield c

    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


async def _make_agent_token(app, *, scopes=("a2a_receive",), origin="taos-deployed"):
    """Register an agent, add its grants, and return (canonical_id, signed JWT)."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    priv, _pub = app.state.agent_registry_keypair

    rec = await registry.register(
        framework="taosmd",
        display_name="Bus Reader",
        origin=origin,
        handle="@bus-reader",
    )
    cid = rec["canonical_id"]
    for scope in scopes:
        await grants.add_grant(cid, scope)
    token = mint_registry_token(cid, priv, user_id="u", framework="taosmd")
    return cid, token


def _bare(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
class TestBusAgentAuth:

    async def test_agent_with_receive_scope_reads_channels(self, bus_client):
        _cid, token = await _make_agent_token(bus_client._app)
        with patch(_BUS_PATCH, return_value=_mock_bus_client({"channels": []})):
            async with _bare(bus_client._app) as bare:
                resp = await bare.get(
                    "/api/a2a/bus/channels",
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    async def test_agent_with_receive_scope_reads_messages(self, bus_client):
        _cid, token = await _make_agent_token(bus_client._app)
        with patch(_BUS_PATCH, return_value=_mock_bus_client({"messages": []})):
            async with _bare(bus_client._app) as bare:
                resp = await bare.get(
                    "/api/a2a/bus/messages",
                    params={"channel": "general"},
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    async def test_agent_without_receive_scope_gets_403(self, bus_client):
        _cid, token = await _make_agent_token(bus_client._app, scopes=("a2a_send",))
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/bus/channels",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    async def test_suspended_agent_gets_403(self, bus_client):
        cid, token = await _make_agent_token(bus_client._app)
        await bus_client._app.state.agent_registry.set_status(cid, "suspended")
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/bus/channels",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    async def test_revoked_agent_gets_403(self, bus_client):
        cid, token = await _make_agent_token(bus_client._app)
        await bus_client._app.state.agent_registry.revoke(cid)
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/bus/messages",
                params={"channel": "general"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    async def test_malformed_token_gets_401(self, bus_client):
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/bus/channels",
                headers={"Authorization": "Bearer not-a-valid-token"},
            )
        assert resp.status_code == 401

    async def test_token_signed_by_different_key_gets_401(self, bus_client):
        # Register the agent so the sub exists, but sign with a foreign key.
        registry = bus_client._app.state.agent_registry
        grants = bus_client._app.state.agent_grants
        rec = await registry.register(framework="taosmd", display_name="Foreign", handle="@foreign")
        cid = rec["canonical_id"]
        await grants.add_grant(cid, "a2a_receive")
        with tempfile.TemporaryDirectory() as d:
            foreign_priv, _foreign_pub = load_or_create_signing_keypair(Path(d))
        bad_token = mint_registry_token(cid, foreign_priv, user_id="u", framework="taosmd")
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/bus/channels",
                headers={"Authorization": f"Bearer {bad_token}"},
            )
        assert resp.status_code == 401

    async def test_valid_sig_unknown_sub_gets_403(self, bus_client):
        # Properly signed by the app key but the sub is not in the registry.
        priv, _pub = bus_client._app.state.agent_registry_keypair
        token = mint_registry_token("ghost-20260101-000000", priv, user_id="u", framework="taosmd")
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/bus/channels",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    async def test_expired_grant_gets_403(self, bus_client):
        from datetime import datetime, timezone, timedelta

        registry = bus_client._app.state.agent_registry
        grants = bus_client._app.state.agent_grants
        rec = await registry.register(framework="taosmd", display_name="Expired", handle="@expired")
        cid = rec["canonical_id"]
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        await grants.add_grant(cid, "a2a_receive", expires_at=past)
        priv, _pub = bus_client._app.state.agent_registry_keypair
        token = mint_registry_token(cid, priv, user_id="u", framework="taosmd")
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/bus/channels",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    async def test_admin_session_still_reads_bus(self, bus_client):
        """Regression: an admin cookie session reads the bus unchanged."""
        with patch(_BUS_PATCH, return_value=_mock_bus_client({"channels": []})):
            resp = await bus_client.get("/api/a2a/bus/channels")
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    async def test_local_token_reads_bus_as_admin(self, bus_client):
        """Regression: a Bearer local token authenticates as admin and is NOT
        parsed as a registry JWT."""
        local_token = bus_client._app.state.auth.get_local_token()
        with patch(_BUS_PATCH, return_value=_mock_bus_client({"channels": []})):
            async with _bare(bus_client._app) as bare:
                resp = await bare.get(
                    "/api/a2a/bus/channels",
                    headers={"Authorization": f"Bearer {local_token}"},
                )
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    async def test_skeleton_key_guard_agent_token_rejected_off_allowlist(self, bus_client):
        """A valid agent JWT (with a2a_receive) must NOT authenticate a
        non-allowlisted protected route -- the token is not a skeleton key."""
        _cid, token = await _make_agent_token(bus_client._app)
        async with _bare(bus_client._app) as bare:
            resp = await bare.get(
                "/api/agents/registry",
                headers={"Authorization": f"Bearer {token}"},
            )
        # Falls through middleware to the session gate: API request -> 401.
        assert resp.status_code == 401


def _mock_bus_post(json_payload: dict, *, raise_on_status: bool = False):
    """A mock httpx.AsyncClient whose async ctx yields a client whose
    .post().json() returns *json_payload*. If *raise_on_status* the mocked
    response.raise_for_status() raises, simulating a bus error."""
    mock_resp = MagicMock()
    if raise_on_status:
        mock_resp.raise_for_status = MagicMock(side_effect=RuntimeError("bus 500"))
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = json_payload

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx, mock_client


@pytest.mark.asyncio
class TestBusAgentSend:
    """Authenticated write path: agents post as themselves, never spoofing."""

    async def test_agent_send_derives_from_from_registry_handle(self, bus_client):
        # _make_agent_token registers handle "@bus-reader".
        _cid, token = await _make_agent_token(bus_client._app, scopes=("a2a_send",))
        ctx, client = _mock_bus_post({"id": 1, "from": "@bus-reader", "thread": "build"})
        with patch(_BUS_PATCH, return_value=ctx):
            async with _bare(bus_client._app) as bare:
                resp = await bare.post(
                    "/api/a2a/bus/send",
                    json={"thread": "build", "body": "hi", "from": "@somebody-else"},
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert resp.status_code == 200
        assert resp.json()["from"] == "@bus-reader"
        # Anti-spoof: the body's "from" is ignored; the bus is called with the
        # agent's own registry handle.
        sent = client.post.call_args.kwargs["json"]
        assert sent["from"] == "@bus-reader"
        assert sent["thread"] == "build" and sent["body"] == "hi"

    async def test_agent_without_send_scope_forbidden(self, bus_client):
        # Only a2a_receive -> cannot send.
        _cid, token = await _make_agent_token(bus_client._app, scopes=("a2a_receive",))
        async with _bare(bus_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/bus/send",
                json={"thread": "build", "body": "hi"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    async def test_no_auth_rejected(self, bus_client):
        # No Bearer header at all: the auth middleware rejects the API request
        # with 401 before it reaches the route (no credentials presented). The
        # route's own 403 covers a *valid* token that lacks the a2a_send grant.
        async with _bare(bus_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/bus/send",
                json={"thread": "build", "body": "hi"},
            )
        assert resp.status_code == 401

    async def test_admin_may_send_with_explicit_from(self, bus_client):
        ctx, client = _mock_bus_post({"id": 2, "from": "@operator", "thread": "general"})
        with patch(_BUS_PATCH, return_value=ctx):
            resp = await bus_client.post(
                "/api/a2a/bus/send",
                json={"thread": "general", "body": "ops note", "from": "@release-bot"},
            )
        assert resp.status_code == 200
        # Admin's explicit from is honored.
        assert client.post.call_args.kwargs["json"]["from"] == "@release-bot"

    async def test_missing_thread_or_body_400(self, bus_client):
        _cid, token = await _make_agent_token(bus_client._app, scopes=("a2a_send",))
        async with _bare(bus_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/bus/send",
                json={"thread": "  ", "body": "hi"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400

    async def test_reply_to_must_be_positive(self, bus_client):
        _cid, token = await _make_agent_token(bus_client._app, scopes=("a2a_send",))
        async with _bare(bus_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/bus/send",
                json={"thread": "build", "body": "hi", "reply_to": 0},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400

    async def test_body_is_trimmed_before_forwarding(self, bus_client):
        _cid, token = await _make_agent_token(bus_client._app, scopes=("a2a_send",))
        ctx, client = _mock_bus_post({"id": 9, "from": "@bus-reader", "thread": "build"})
        with patch(_BUS_PATCH, return_value=ctx):
            async with _bare(bus_client._app) as bare:
                resp = await bare.post(
                    "/api/a2a/bus/send",
                    json={"thread": "build", "body": "  padded  "},
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert resp.status_code == 200
        assert client.post.call_args.kwargs["json"]["body"] == "padded"

    async def test_admin_from_control_chars_stripped(self, bus_client):
        ctx, client = _mock_bus_post({"id": 10, "from": "@x", "thread": "general"})
        with patch(_BUS_PATCH, return_value=ctx):
            resp = await bus_client.post(
                "/api/a2a/bus/send",
                json={"thread": "general", "body": "hi", "from": "@evil\nInjected: x"},
            )
        assert resp.status_code == 200
        # Newline/control chars removed so nothing can be injected downstream.
        assert "\n" not in client.post.call_args.kwargs["json"]["from"]

    async def test_bus_error_surfaces_502(self, bus_client):
        _cid, token = await _make_agent_token(bus_client._app, scopes=("a2a_send",))
        ctx, _client = _mock_bus_post({}, raise_on_status=True)
        with patch(_BUS_PATCH, return_value=ctx):
            async with _bare(bus_client._app) as bare:
                resp = await bare.post(
                    "/api/a2a/bus/send",
                    json={"thread": "build", "body": "hi"},
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert resp.status_code == 502
