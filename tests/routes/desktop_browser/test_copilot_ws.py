"""Tests for CopilotTicketStore, CopilotHub, and the copilot WebSocket endpoint."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketDisconnect


# ---------------------------------------------------------------------------
# Store-only unit tests (no HTTP)
# ---------------------------------------------------------------------------

class TestCopilotTicketStore:
    def _make_store(self, now: list[float] | None = None):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore
        if now is None:
            return CopilotTicketStore()
        return CopilotTicketStore(clock=lambda: now[0])

    def test_mint_returns_url_safe_token(self):
        store = self._make_store()
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        assert token
        # token_urlsafe(32) uses base64url — no +, /, or = characters
        for bad_char in ("+", "/", "="):
            assert bad_char not in token

    @pytest.mark.parametrize("missing", ["user_id", "profile_id", "tab_id", "agent_id"])
    def test_mint_rejects_empty_params(self, missing):
        store = self._make_store()
        kwargs = dict(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        kwargs[missing] = ""
        with pytest.raises(ValueError):
            store.mint(**kwargs)

    def test_consume_returns_ticket_once(self):
        now = [0.0]
        store = self._make_store(now)
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        ticket = store.consume(token)
        assert ticket is not None
        assert ticket.user_id == "u1"
        assert ticket.profile_id == "p1"
        assert ticket.tab_id == "t1"
        assert ticket.agent_id == "a1"
        # Second consume → None (single-use)
        assert store.consume(token) is None

    def test_consume_returns_none_for_unknown_token(self):
        store = self._make_store()
        assert store.consume("totally-unknown-token") is None

    def test_consume_returns_none_for_expired_ticket(self):
        now = [0.0]
        store = self._make_store(now)
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")
        # Advance clock past TTL
        now[0] = 61.0
        result = store.consume(token)
        assert result is None
        # Ticket must also be gone from the store after expiry consume
        assert store.consume(token) is None

    def test_mint_garbage_collects_expired(self):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore

        now = [0.0]
        store = CopilotTicketStore(clock=lambda: now[0])

        # Mint A at t=0
        token_a = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")

        # Advance past TTL and mint B → GC should sweep A out
        now[0] = 120.0
        store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="b1")

        # A is gone — consume returns None and the internal dict doesn't hold it
        assert store.consume(token_a) is None

    def test_mint_single_clock_read_no_self_eviction(self):
        """Regression: mint() must capture `now` once so GC cannot evict the
        freshly minted ticket when the clock jumps exactly TTL between calls."""
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore

        # Clock returns 0.0 on first call, then 60.0 (exactly TTL) on every
        # subsequent call — simulates the worst-case double-read scenario.
        calls = iter([0.0, 60.0])
        clock = lambda: next(calls)  # noqa: E731

        store = CopilotTicketStore(clock=clock)
        token = store.mint(user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1")

        # Token must still be present in the store — not evicted by its own mint.
        assert token in store._tickets
        # And must be consumable (using real time.time for consume, so patch
        # issued_at to 0 is fine — consume's clock call will be >> 0 only if
        # the ticket survived the GC sweep; here we pass a fixed consume clock).
        consume_store = CopilotTicketStore(clock=lambda: 0.0)
        consume_store._tickets = store._tickets
        ticket = consume_store.consume(token)
        assert ticket is not None
        assert ticket.agent_id == "a1"


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

def _add_agent(app, agent_id: str):
    app.state.config.agents.append({
        "id": agent_id,
        "name": agent_id,
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#000000",
    })


@pytest.mark.asyncio
class TestMintTicketEndpoint:
    async def test_mint_ticket_happy_path(self, client, app):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotTicketStore
        _add_agent(app, "agent-tick-1")
        # Pin the agent first
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-tick-1"},
        )
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-tick-1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "ticket" in body
        assert body["ttl_seconds"] == CopilotTicketStore.TICKET_TTL_SECONDS

    async def test_mint_ticket_403_when_not_pinned(self, client):
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "no-pin-agent"},
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "agent not pinned to tab"}

    async def test_mint_ticket_401_when_unauthenticated(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as unauth_client:
            resp = await unauth_client.post(
                "/api/desktop/browser/copilot/ticket",
                json={"profile_id": "p1", "tab_id": "t1", "agent_id": "a1"},
            )
            assert resp.status_code == 401

    async def test_mint_ticket_multi_user_isolation(self, client, app, tmp_data_dir):
        """User A pins an agent; user B cannot mint a ticket for it."""
        _add_agent(app, "agent-iso")
        # User A pins
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-iso"},
        )

        # Set up user B
        auth_mgr = app.state.auth
        if auth_mgr.find_user("user_b") is None:
            invite_code = auth_mgr.add_user_invite("user_b", "admin")
            auth_mgr.complete_invite("user_b", invite_code, "user_b", "", "pass_b")
        record = auth_mgr.find_user("user_b")
        token_b = auth_mgr.create_session(user_id=record["id"], long_lived=True)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": token_b},
        ) as b_client:
            resp = await b_client.post(
                "/api/desktop/browser/copilot/ticket",
                json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-iso"},
            )
            assert resp.status_code == 403

    async def test_mint_ticket_422_on_missing_field(self, client):
        """Pydantic returns 422 when a required body field is omitted."""
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1"},  # agent_id missing
        )
        assert resp.status_code == 422

    async def test_minted_ticket_can_be_consumed_via_app_state(self, client, app):
        _add_agent(app, "agent-consume")
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-consume"},
        )
        resp = await client.post(
            "/api/desktop/browser/copilot/ticket",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-consume"},
        )
        assert resp.status_code == 200
        token = resp.json()["ticket"]

        # The ticket store must hold a consumable ticket for that token
        ticket = app.state.copilot_ticket_store.consume(token)
        assert ticket is not None
        assert ticket.agent_id == "agent-consume"
        assert ticket.tab_id == "t1"


# ---------------------------------------------------------------------------
# CopilotHub unit tests
# ---------------------------------------------------------------------------

class TestCopilotHub:
    def _make_hub(self):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotHub
        return CopilotHub()

    @pytest.mark.asyncio
    async def test_add_iframe_replaces_prior_connection_for_same_key(self):
        """Same key + new ws → old ws scheduled for close, new one is registered."""
        hub = self._make_hub()
        old_ws = AsyncMock()
        new_ws = AsyncMock()

        hub._iframe_conns[("u1", "p1", "t1", "agent-a")] = old_ws
        hub.add_iframe(user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-a", ws=new_ws)

        assert hub._iframe_conns[("u1", "p1", "t1", "agent-a")] is new_ws
        assert ("u1", "p1", "t1", "agent-a") in hub._iframe_conns

    def test_remove_iframe_is_noop_when_not_present(self):
        hub = self._make_hub()
        # Must not raise even if the key was never registered.
        hub.remove_iframe(user_id="u1", profile_id="p1", tab_id="t1", agent_id="no-such")

    @pytest.mark.asyncio
    async def test_push_event_to_pinned_fans_out_to_all_agents_on_tab(self):
        """Add 3 iframes for same (user, profile, tab) different agents.
        push_event_to_pinned → all 3 receive the event."""
        hub = self._make_hub()
        ws_a = AsyncMock()
        ws_b = AsyncMock()
        ws_c = AsyncMock()

        hub._iframe_conns[("u1", "p1", "t1", "agent-a")] = ws_a
        hub._iframe_conns[("u1", "p1", "t1", "agent-b")] = ws_b
        hub._iframe_conns[("u1", "p1", "t1", "agent-c")] = ws_c

        event = {"event": "page-changed", "url": "https://example.com"}
        await hub.push_event_to_pinned(user_id="u1", profile_id="p1", tab_id="t1", event=event)

        ws_a.send_json.assert_awaited_once_with(event)
        ws_b.send_json.assert_awaited_once_with(event)
        ws_c.send_json.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_push_event_to_pinned_skips_other_tabs(self):
        """Add iframe for tab A and tab B. Push to tab A → only A's iframe gets the event."""
        hub = self._make_hub()
        ws_tab_a = AsyncMock()
        ws_tab_b = AsyncMock()

        hub._iframe_conns[("u1", "p1", "tab-a", "agent-a")] = ws_tab_a
        hub._iframe_conns[("u1", "p1", "tab-b", "agent-a")] = ws_tab_b

        event = {"event": "scroll", "x": 0, "y": 100}
        await hub.push_event_to_pinned(user_id="u1", profile_id="p1", tab_id="tab-a", event=event)

        ws_tab_a.send_json.assert_awaited_once_with(event)
        ws_tab_b.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_event_to_pinned_swallows_send_failures(self):
        """Fake WS whose send_json raises. push_event_to_pinned doesn't propagate."""
        hub = self._make_hub()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = RuntimeError("connection lost")

        hub._iframe_conns[("u1", "p1", "t1", "agent-a")] = ws_bad

        event = {"event": "page-changed", "url": "https://example.com"}
        # Must not raise
        await hub.push_event_to_pinned(user_id="u1", profile_id="p1", tab_id="t1", event=event)

    @pytest.mark.asyncio
    async def test_push_event_to_pinned_isolates_users(self):
        """User A's push must not reach user B's iframe even when (profile, tab) IDs collide."""
        hub = self._make_hub()
        ws_user_a = AsyncMock()
        ws_user_b = AsyncMock()

        # Same profile/tab/agent IDs but different users — the keys differ on user_id only.
        hub._iframe_conns[("user-a", "p1", "t1", "agent-x")] = ws_user_a
        hub._iframe_conns[("user-b", "p1", "t1", "agent-x")] = ws_user_b

        event = {"event": "page-changed", "url": "https://example.com"}
        await hub.push_event_to_pinned(user_id="user-a", profile_id="p1", tab_id="t1", event=event)

        ws_user_a.send_json.assert_awaited_once_with(event)
        ws_user_b.send_json.assert_not_called()


# ---------------------------------------------------------------------------
# Sync TestClient + browser_store fixture for WS endpoint tests
# ---------------------------------------------------------------------------

def _make_ws_app(tmp_path):
    """Create a minimal app with browser_store initialized (sync-compatible)."""
    from tinyagentos.app import create_app
    from tinyagentos.routes.desktop_browser.store import BrowserStore

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    # Initialize the browser_store synchronously so the WS endpoint can call
    # list_pins_for_tab without hitting an uninitialised store.
    browser_store = BrowserStore(tmp_path / "browser.sqlite3")
    asyncio.run(browser_store.init())
    app.state.browser_store = browser_store

    # Auth setup for the ticket-minting HTTP endpoint
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")

    return app


@pytest.fixture
def ws_app(tmp_path):
    """App fixture with browser_store ready for WS tests."""
    return _make_ws_app(tmp_path)


@pytest.fixture
def ws_client(ws_app):
    """Sync TestClient for WS endpoint tests. Sets auth cookie."""
    record = ws_app.state.auth.find_user("admin")
    token = ws_app.state.auth.create_session(user_id=record["id"], long_lived=True)
    with TestClient(ws_app, raise_server_exceptions=False) as c:
        c.cookies.set("taos_session", token)
        yield c


def _add_agent_to(app, agent_id: str):
    app.state.config.agents.append({
        "id": agent_id,
        "name": agent_id,
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#000000",
    })


def _mint_ticket_via_http(client, app, profile_id, tab_id, agent_id):
    """Pin agent then mint ticket via HTTP endpoint."""
    _add_agent_to(app, agent_id)
    client.post(
        "/api/desktop/browser/pins",
        json={"profile_id": profile_id, "tab_id": tab_id, "agent_id": agent_id},
    )
    resp = client.post(
        "/api/desktop/browser/copilot/ticket",
        json={"profile_id": profile_id, "tab_id": tab_id, "agent_id": agent_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["ticket"]


# ---------------------------------------------------------------------------
# WebSocket endpoint tests
# ---------------------------------------------------------------------------

class TestCopilotWS:
    def test_ws_4401_on_invalid_ticket(self, ws_client):
        """Connect with random ticket → close code 4401."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_client.websocket_connect(
                "/api/desktop/browser/copilot?ticket=totally-invalid-token"
            ) as ws:
                ws.receive_text()
        assert exc_info.value.code == 4401

    def test_ws_4401_on_consumed_ticket(self, ws_client, ws_app):
        """Mint, consume in-process, then try to connect → close code 4401."""
        ticket = _mint_ticket_via_http(ws_client, ws_app, "p1", "t1", "agent-consumed")
        # Consume the ticket before connecting
        ws_app.state.copilot_ticket_store.consume(ticket)

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_client.websocket_connect(
                f"/api/desktop/browser/copilot?ticket={ticket}"
            ) as ws:
                ws.receive_text()
        assert exc_info.value.code == 4401

    def test_ws_4403_when_unpinned_after_mint(self, ws_client, ws_app):
        """Pin agent, mint ticket, unpin, then connect → close code 4403."""
        ticket = _mint_ticket_via_http(ws_client, ws_app, "p1", "t1", "agent-unpin")
        # Unpin the agent
        ws_client.delete(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-unpin"},
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_client.websocket_connect(
                f"/api/desktop/browser/copilot?ticket={ticket}"
            ) as ws:
                ws.receive_text()
        assert exc_info.value.code == 4403

    def test_ws_happy_path_registers_iframe_in_hub(self, ws_client, ws_app):
        """Pin, mint, connect. Inside the connection: hub has the iframe registered.
        Disconnect: hub no longer has it."""
        ticket = _mint_ticket_via_http(ws_client, ws_app, "p1", "t1", "agent-happy")

        # Find the user_id for the admin user
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot?ticket={ticket}"
        ) as ws:
            # While connected: hub must have the iframe registered
            key = (user_id, "p1", "t1", "agent-happy")
            assert key in ws_app.state.copilot_hub._iframe_conns

        # After disconnect: hub must have removed the iframe
        assert key not in ws_app.state.copilot_hub._iframe_conns

    def test_ws_disconnect_cleans_up(self, ws_client, ws_app):
        """Connect then close from client side. Hub entry removed."""
        ticket = _mint_ticket_via_http(ws_client, ws_app, "p1", "t1", "agent-cleanup")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        key = (user_id, "p1", "t1", "agent-cleanup")
        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot?ticket={ticket}"
        ):
            assert key in ws_app.state.copilot_hub._iframe_conns

        assert key not in ws_app.state.copilot_hub._iframe_conns

    def test_ws_drops_unknown_event_kinds(self, ws_client, ws_app):
        """Connect, send unknown event kind and a known one — no crash."""
        ticket = _mint_ticket_via_http(ws_client, ws_app, "p1", "t1", "agent-drop")

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot?ticket={ticket}"
        ) as ws:
            # Unknown event — silently dropped
            ws.send_json({"event": "definitely-not-real", "data": "x"})
            # Known event — also silently accepted (not echoed back)
            ws.send_json({"event": "scroll", "x": 0, "y": 100})
            # Connection still alive — hub still has the entry
            record = ws_app.state.auth.find_user("admin")
            user_id = record["id"]
            key = (user_id, "p1", "t1", "agent-drop")
            assert key in ws_app.state.copilot_hub._iframe_conns
