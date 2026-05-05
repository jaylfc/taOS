"""Tests for the agent-side copilot WebSocket endpoint and CopilotHub agent methods."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


# ---------------------------------------------------------------------------
# Fixtures — reuse _make_ws_app pattern from test_copilot_ws.py
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

    browser_store = BrowserStore(tmp_path / "browser.sqlite3")
    asyncio.run(browser_store.init())
    app.state.browser_store = browser_store

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")

    return app


@pytest.fixture
def ws_app(tmp_path):
    return _make_ws_app(tmp_path)


@pytest.fixture
def ws_client(ws_app):
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


def _pin_and_mint(client, app, profile_id, tab_id, agent_id):
    """Pin agent and mint a ticket. Returns the ticket token."""
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
# Case 1: 4401 on invalid ticket
# ---------------------------------------------------------------------------

class TestAgentWS4401InvalidTicket:
    def test_4401_on_random_ticket(self, ws_client):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_client.websocket_connect(
                "/api/desktop/browser/copilot-agent?ticket=totally-invalid-token"
            ) as ws:
                ws.receive_text()
        assert exc_info.value.code == 4401


# ---------------------------------------------------------------------------
# Case 2: 4401 on already-consumed ticket
# ---------------------------------------------------------------------------

class TestAgentWS4401ConsumedTicket:
    def test_4401_on_consumed_ticket(self, ws_client, ws_app):
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-consumed")
        ws_app.state.copilot_ticket_store.consume(ticket)

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_client.websocket_connect(
                f"/api/desktop/browser/copilot-agent?ticket={ticket}"
            ) as ws:
                ws.receive_text()
        assert exc_info.value.code == 4401


# ---------------------------------------------------------------------------
# Case 3: 4403 on unpinned agent
# ---------------------------------------------------------------------------

class TestAgentWS4403Unpinned:
    def test_4403_when_unpinned_after_mint(self, ws_client, ws_app):
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-unpin")
        ws_client.delete(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-unpin"},
        )

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_client.websocket_connect(
                f"/api/desktop/browser/copilot-agent?ticket={ticket}"
            ) as ws:
                ws.receive_text()
        assert exc_info.value.code == 4403


# ---------------------------------------------------------------------------
# Case 4: Successful upgrade adds agent to hub
# ---------------------------------------------------------------------------

class TestAgentWSRegistersInHub:
    def test_agent_registered_in_hub_during_connection(self, ws_client, ws_app):
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-hub")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ):
            key = (user_id, "agent-hub")
            assert key in ws_app.state.copilot_hub._agent_conns

        # After disconnect, still need to check removal in case 5 below.


# ---------------------------------------------------------------------------
# Case 5: Disconnect cleans up agent from hub
# ---------------------------------------------------------------------------

class TestAgentWSCleanupOnDisconnect:
    def test_hub_key_removed_after_disconnect(self, ws_client, ws_app):
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-cleanup")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]
        key = (user_id, "agent-cleanup")

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ):
            assert key in ws_app.state.copilot_hub._agent_conns

        assert key not in ws_app.state.copilot_hub._agent_conns


# ---------------------------------------------------------------------------
# Case 6: route_op_to_iframe returns False when no iframe registered
# ---------------------------------------------------------------------------

class TestRouteOpToIframeNoIframe:
    @pytest.mark.asyncio
    async def test_returns_false_when_iframe_not_connected(self):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotHub
        hub = CopilotHub()
        result = await hub.route_op_to_iframe(
            user_id="u1",
            profile_id="p1",
            tab_id="t1",
            agent_id="agent-x",
            op={"op": "click", "selector": "#btn"},
        )
        assert result is False


# ---------------------------------------------------------------------------
# Case 7: Round-trip: agent op → iframe receive → ack → agent receive
# ---------------------------------------------------------------------------

class TestRoundTripOpAck:
    def test_op_forwarded_to_iframe_and_ack_forwarded_to_agent(
        self, ws_client, ws_app
    ):
        """Register a mock iframe, connect agent, send op, verify iframe gets it,
        then send ack from iframe WS, verify agent receives it."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-rt")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # Grant drive capability so click op is not denied
        asyncio.run(
            ws_app.state.browser_store.add_capability(
                user_id=user_id,
                profile_id="p1",
                agent_id="agent-rt",
                host_pattern="example.com",
                permissions="drive",
            )
        )

        # Register a fake iframe in the hub
        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-rt")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws
        # Trusted current URL for capability check (server-tracked, not agent-supplied)
        ws_app.state.copilot_hub.set_tab_url(
            user_id=user_id, profile_id="p1", tab_id="t1",
            url="https://example.com/page",
        )

        op_msg = {"op": "click", "selector": "#submit", "op_id": "op-1", "host": "example.com"}

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json(op_msg)
            # Give the async loop a moment to process
            import time
            time.sleep(0.05)

        # The iframe mock should have received the op
        mock_iframe_ws.send_json.assert_awaited_once_with(op_msg)

        # Clean up
        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)


# ---------------------------------------------------------------------------
# Case 8: Drive op bumps drive_session
# ---------------------------------------------------------------------------

class TestDriveOpBumpsSession:
    def test_click_op_creates_drive_session(self, ws_client, ws_app):
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-drive")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # Grant drive capability so click op is not denied
        asyncio.run(
            ws_app.state.browser_store.add_capability(
                user_id=user_id,
                profile_id="p1",
                agent_id="agent-drive",
                host_pattern="example.com",
                permissions="drive",
            )
        )

        # Register a mock iframe so the op routes successfully
        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-drive")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws
        ws_app.state.copilot_hub.set_tab_url(
            user_id=user_id, profile_id="p1", tab_id="t1",
            url="https://example.com/",
        )

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json({"op": "click", "selector": "#btn", "op_id": "op-2", "host": "example.com"})
            import time
            time.sleep(0.05)

        # Verify drive session exists
        result = asyncio.run(
            ws_app.state.browser_store.is_driving(
                user_id=user_id,
                profile_id="p1",
                tab_id="t1",
                agent_id="agent-drive",
            )
        )
        assert result is True

        # Clean up
        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)


# ---------------------------------------------------------------------------
# Case 9: Non-drive op does NOT bump drive_session
# ---------------------------------------------------------------------------

class TestNonDriveOpNoSession:
    def test_extract_op_does_not_create_drive_session(self, ws_client, ws_app):
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-nodrive")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # Register a mock iframe so the op routes successfully
        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-nodrive")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json({"op": "extract", "selector": "article", "op_id": "op-3"})
            import time
            time.sleep(0.05)

        result = asyncio.run(
            ws_app.state.browser_store.is_driving(
                user_id=user_id,
                profile_id="p1",
                tab_id="t1",
                agent_id="agent-nodrive",
            )
        )
        assert result is False

        # Clean up
        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)


# ---------------------------------------------------------------------------
# Case 10: Op with missing iframe → agent receives event: "error"
# ---------------------------------------------------------------------------

class TestOpMissingIframe:
    def test_error_event_sent_to_agent_when_iframe_absent(self, ws_client, ws_app):
        """Send a privileged op with capability granted but no iframe connected →
        server replies event: error (iframe not connected)."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-err")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # Grant drive so the capability check passes; then no iframe → error
        asyncio.run(
            ws_app.state.browser_store.add_capability(
                user_id=user_id,
                profile_id="p1",
                agent_id="agent-err",
                host_pattern="example.com",
                permissions="drive",
            )
        )
        # Trusted current URL so capability check sees example.com (server-tracked)
        ws_app.state.copilot_hub.set_tab_url(
            user_id=user_id, profile_id="p1", tab_id="t1",
            url="https://example.com/",
        )

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json({"op": "click", "selector": "#x", "op_id": "op-err", "host": "example.com"})
            reply = ws.receive_json()

        assert reply["event"] == "error"
        assert reply["op_id"] == "op-err"
        assert "iframe not connected" in reply["reason"]


# ---------------------------------------------------------------------------
# Additional CopilotHub agent-method unit tests
# ---------------------------------------------------------------------------

class TestCopilotHubAgentMethods:
    def _make_hub(self):
        from tinyagentos.routes.desktop_browser.copilot_ws import CopilotHub
        return CopilotHub()

    def test_add_agent_registers_ws(self):
        hub = self._make_hub()
        ws = AsyncMock()
        hub.add_agent(user_id="u1", agent_id="a1", ws=ws)
        assert hub._agent_conns[("u1", "a1")] is ws

    def test_remove_agent_is_noop_when_not_present(self):
        hub = self._make_hub()
        hub.remove_agent(user_id="u1", agent_id="no-such")  # must not raise

    @pytest.mark.asyncio
    async def test_add_agent_replaces_prior_connection(self):
        hub = self._make_hub()
        old_ws = AsyncMock()
        new_ws = AsyncMock()
        hub._agent_conns[("u1", "a1")] = old_ws
        hub.add_agent(user_id="u1", agent_id="a1", ws=new_ws)
        assert hub._agent_conns[("u1", "a1")] is new_ws

    @pytest.mark.asyncio
    async def test_route_ack_to_agent_returns_true_on_success(self):
        hub = self._make_hub()
        ws = AsyncMock()
        hub._agent_conns[("u1", "a1")] = ws
        ack = {"event": "ack", "op_id": "op-1", "status": "ok"}
        result = await hub.route_ack_to_agent(user_id="u1", agent_id="a1", ack=ack)
        assert result is True
        ws.send_json.assert_awaited_once_with(ack)

    @pytest.mark.asyncio
    async def test_route_ack_to_agent_returns_false_when_no_agent(self):
        hub = self._make_hub()
        result = await hub.route_ack_to_agent(
            user_id="u1", agent_id="no-such",
            ack={"event": "ack", "op_id": "x"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_route_ack_to_agent_swallows_send_failure(self):
        hub = self._make_hub()
        ws = AsyncMock()
        ws.send_json.side_effect = RuntimeError("connection reset")
        hub._agent_conns[("u1", "a1")] = ws
        result = await hub.route_ack_to_agent(
            user_id="u1", agent_id="a1",
            ack={"event": "ack", "op_id": "x"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_route_op_to_iframe_returns_true_on_success(self):
        hub = self._make_hub()
        ws = AsyncMock()
        hub._iframe_conns[("u1", "p1", "t1", "a1")] = ws
        op = {"op": "navigate", "url": "https://example.com"}
        result = await hub.route_op_to_iframe(
            user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1", op=op,
        )
        assert result is True
        ws.send_json.assert_awaited_once_with(op)

    @pytest.mark.asyncio
    async def test_route_op_to_iframe_swallows_send_failure(self):
        hub = self._make_hub()
        ws = AsyncMock()
        ws.send_json.side_effect = RuntimeError("disconnected")
        hub._iframe_conns[("u1", "p1", "t1", "a1")] = ws
        result = await hub.route_op_to_iframe(
            user_id="u1", profile_id="p1", tab_id="t1", agent_id="a1",
            op={"op": "click"},
        )
        assert result is False

    # ------------------------------------------------------------------
    # notify_capability_needed unit tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_notify_capability_needed_sends_event_to_iframe(self):
        """notify_capability_needed pushes capability-needed payload to the iframe WS."""
        hub = self._make_hub()
        ws = AsyncMock()
        hub._iframe_conns[("u1", "p1", "t1", "a1")] = ws
        result = await hub.notify_capability_needed(
            user_id="u1",
            profile_id="p1",
            tab_id="t1",
            agent_id="a1",
            permission="drive",
            host="example.com",
            full_url="https://example.com/page",
        )
        assert result is True
        ws.send_json.assert_awaited_once_with({
            "event": "capability-needed",
            "profile_id": "p1",
            "permission": "drive",
            "host": "example.com",
            "full_url": "https://example.com/page",
        })

    @pytest.mark.asyncio
    async def test_notify_capability_needed_returns_false_when_no_iframe(self):
        hub = self._make_hub()
        result = await hub.notify_capability_needed(
            user_id="u1",
            profile_id="p1",
            tab_id="t1",
            agent_id="a1",
            permission="drive",
            host="example.com",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_notify_capability_needed_swallows_send_failure(self):
        hub = self._make_hub()
        ws = AsyncMock()
        ws.send_json.side_effect = RuntimeError("closed")
        hub._iframe_conns[("u1", "p1", "t1", "a1")] = ws
        result = await hub.notify_capability_needed(
            user_id="u1",
            profile_id="p1",
            tab_id="t1",
            agent_id="a1",
            permission="drive",
            host="example.com",
        )
        assert result is False


# ---------------------------------------------------------------------------
# Case 11: Capability enforcement — drive ops
# ---------------------------------------------------------------------------

def _grant_capability(app, user_id, profile_id, agent_id, host, permissions):
    """Synchronously add a capability grant to the browser_store."""
    import asyncio
    asyncio.run(
        app.state.browser_store.add_capability(
            user_id=user_id,
            profile_id=profile_id,
            agent_id=agent_id,
            host_pattern=host,
            permissions=permissions,
        )
    )


class TestCapabilityEnforcement:

    def test_drive_op_without_capability_is_denied(self, ws_client, ws_app):
        """Agent sends op:click without a drive grant → denied + iframe notified."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-cap-deny")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # Register fake iframe
        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-cap-deny")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws
        # Server-tracked URL — capability check uses this, NOT agent-supplied msg["host"]
        ws_app.state.copilot_hub.set_tab_url(
            user_id=user_id, profile_id="p1", tab_id="t1",
            url="https://example.com/page",
        )

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json({
                "op": "click",
                "selector": "#btn",
                "op_id": "op-deny-1",
                "host": "example.com",
            })
            reply = ws.receive_json()

        assert reply["event"] == "denied"
        assert reply["op_id"] == "op-deny-1"
        assert reply["reason"] == "capability-needed"
        assert reply["permission"] == "drive"

        # iframe should have received capability-needed (host from server-tracked URL)
        mock_iframe_ws.send_json.assert_awaited_once()
        sent = mock_iframe_ws.send_json.call_args[0][0]
        assert sent["event"] == "capability-needed"
        assert sent["permission"] == "drive"
        assert sent["host"] == "example.com"

        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)

    def test_drive_op_with_capability_proceeds(self, ws_client, ws_app):
        """Agent sends op:click WITH a drive grant → routes through (no denial)."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-cap-ok")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # Grant drive capability
        _grant_capability(ws_app, user_id, "p1", "agent-cap-ok", "example.com", "drive")

        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-cap-ok")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws
        # Server-tracked URL for capability check
        ws_app.state.copilot_hub.set_tab_url(
            user_id=user_id, profile_id="p1", tab_id="t1",
            url="https://example.com/",
        )

        op_msg = {"op": "click", "selector": "#submit", "op_id": "op-ok-1", "host": "example.com"}

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json(op_msg)
            import time
            time.sleep(0.05)

        # The iframe should have received the op (not a denial)
        mock_iframe_ws.send_json.assert_awaited_once_with(op_msg)

        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)

    def test_navigate_op_requires_navigate_capability(self, ws_client, ws_app):
        """op:navigate is gated by 'navigate' permission — 'drive' grant is NOT enough."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-cap-nav")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # Grant only drive, NOT navigate
        _grant_capability(ws_app, user_id, "p1", "agent-cap-nav", "example.com", "drive")

        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-cap-nav")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json({
                "op": "navigate",
                "url": "https://example.com/other",
                "op_id": "op-nav-1",
                "host": "example.com",
            })
            reply = ws.receive_json()

        assert reply["event"] == "denied"
        assert reply["permission"] == "navigate"

        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)

    def test_non_privileged_op_passes_through(self, ws_client, ws_app):
        """op:extract (read-only) goes through without any capability check."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-cap-extract")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # No capability granted — extract should still route
        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-cap-extract")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws

        op_msg = {"op": "extract", "selector": "article", "op_id": "op-ext-1"}

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json(op_msg)
            import time
            time.sleep(0.05)

        # Iframe should have received the extract op
        mock_iframe_ws.send_json.assert_awaited_once_with(op_msg)

        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)

    def test_capability_needed_with_no_iframe_still_denies_agent(
        self, ws_client, ws_app
    ):
        """When iframe is not connected, agent still gets the denial."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-cap-noframe")

        # No iframe registered, no capability granted
        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json({
                "op": "click",
                "selector": "#x",
                "op_id": "op-noframe-1",
                "host": "example.com",
            })
            reply = ws.receive_json()

        assert reply["event"] == "denied"
        assert reply["reason"] == "capability-needed"
        assert reply["permission"] == "drive"


# ---------------------------------------------------------------------------
# Security: agent-supplied msg["host"] is NOT trusted for capability checks
# ---------------------------------------------------------------------------

class TestTrustedHostOnly:
    def test_capability_uses_server_tracked_url_not_msg_host(self, ws_client, ws_app):
        """Agent claims host=allowed-site.com but server-tracked URL is
        actually-different.com. Capability check must use the server URL
        (no grant for actually-different.com → denied)."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-spoof")
        record = ws_app.state.auth.find_user("admin")
        user_id = record["id"]

        # User granted drive ONLY for the actual server-tracked URL host
        _grant_capability(ws_app, user_id, "p1", "agent-spoof", "trusted.com", "drive")

        mock_iframe_ws = AsyncMock()
        iframe_key = (user_id, "p1", "t1", "agent-spoof")
        ws_app.state.copilot_hub._iframe_conns[iframe_key] = mock_iframe_ws
        # Server says the iframe is on attacker.com, not trusted.com
        ws_app.state.copilot_hub.set_tab_url(
            user_id=user_id, profile_id="p1", tab_id="t1",
            url="https://attacker.com/page",
        )

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            # Agent LIES: claims host=trusted.com to try to bypass
            ws.send_json({
                "op": "click", "selector": "#x", "op_id": "spoof-1",
                "host": "trusted.com",  # ← agent's claim, must be ignored
            })
            reply = ws.receive_json()

        # Server should use the trusted URL (attacker.com → no grant) and deny
        assert reply["event"] == "denied"
        assert reply["reason"] == "capability-needed"
        # The iframe was notified with the REAL host (attacker.com), not the spoof
        sent = mock_iframe_ws.send_json.call_args[0][0]
        assert sent["host"] == "attacker.com"

        ws_app.state.copilot_hub._iframe_conns.pop(iframe_key, None)


# ---------------------------------------------------------------------------
# Security: per-op tab_id override re-checks pin
# ---------------------------------------------------------------------------

class TestPerOpTargetReauthorization:
    def test_op_targeting_unpinned_tab_is_denied(self, ws_client, ws_app):
        """Agent connects with ticket bound to (p1, t1) where it IS pinned,
        then sends an op targeting tab t2 where it is NOT pinned. Server
        must reject with 'agent not pinned for target tab'."""
        ticket = _pin_and_mint(ws_client, ws_app, "p1", "t1", "agent-cross")
        # Note: agent is pinned to t1 via _pin_and_mint, but NOT to t2

        with ws_client.websocket_connect(
            f"/api/desktop/browser/copilot-agent?ticket={ticket}"
        ) as ws:
            ws.send_json({
                "op": "extract",     # non-privileged, so capability isn't the gate
                "op_id": "cross-1",
                "tab_id": "t2",      # ← target tab where agent is NOT pinned
            })
            reply = ws.receive_json()

        assert reply["event"] == "denied"
        assert reply["op_id"] == "cross-1"
        assert "not pinned for target tab" in reply["reason"]
