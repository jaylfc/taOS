"""Tests for the shortcut terminal WebSocket route (Tasks 20-21).

Task 20: PTY bridge — cookie validation, scope check, basic data flow.
Task 21: tui shortcut spawns PTY with bash -lc <command>.
"""
from __future__ import annotations

import secrets
import time
from unittest.mock import patch

import pytest

from tinyagentos.routes.shortcut_proxy import _sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_terminal_session(agent_id: str, idx: int, scope: str = "container-terminal") -> str:
    """Insert a valid terminal session into _sessions and return the session_id."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "agent_id": agent_id,
        "shortcut_idx": idx,
        "scope": scope,
        "expires_at": time.monotonic() + 300,
    }
    return session_id


class FakePtyHandle:
    """Synchronous fake PTY handle matching the real PtyHandle interface."""

    def read(self, size: int = 4096) -> bytes:
        return b"$ "

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Task 20 — basic terminal route wiring
# ---------------------------------------------------------------------------

class TestTerminalRouteExists:
    def test_terminal_ws_route_registered(self, app):
        """The WS route /shortcut/terminal/{agent_name}/{idx} must be registered."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        expected = "/shortcut/terminal/{agent_name}/{idx}"
        assert expected in routes, f"Terminal WS route not found. Routes: {routes}"


class TestTerminalMissingCookieRejected:
    def test_no_cookie_closes_connection(self, test_client, seeded_agent_factory):
        """WebSocket with no taos_shortcut cookie must be rejected."""
        shortcuts = [
            {
                "kind": "container-terminal",
                "label": "Shell",
                "icon": "terminal",
                "requires_capability": "agent.shell",
            }
        ]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        agent_id = agent["id"]

        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/shortcut/terminal/{agent_id}/0"
            ) as ws:
                ws.receive_text()


class TestTerminalPtyBridge:
    def test_pty_output_forwarded_to_client(self, test_client, seeded_agent_factory):
        """Data from FakePtyHandle.read() must be forwarded over the WebSocket."""
        shortcuts = [
            {
                "kind": "container-terminal",
                "label": "Shell",
                "icon": "terminal",
                "requires_capability": "agent.shell",
            }
        ]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        agent_id = agent["id"]
        session_id = _make_terminal_session(agent_id, 0)

        fake_pty = FakePtyHandle()

        with patch(
            "tinyagentos.routes.shortcut_proxy._get_container_pty",
            return_value=fake_pty,
        ):
            test_client.cookies.set("taos_shortcut", session_id)
            try:
                with test_client.websocket_connect(
                    f"/shortcut/terminal/{agent_id}/0"
                ) as ws:
                    data = ws.receive_text()
                    assert data == "$ "
            finally:
                test_client.cookies.clear()
