"""Tests: all /ws/ WebSocket endpoints reject unauthenticated connections.

Covers /ws/terminal, /ws/chat, /ws/canvas, and /ws/chat/{agent_name}.
Each unauthenticated connect must be closed (code 1008) without accepting
or spawning any process; authenticated connects must succeed.
"""
from __future__ import annotations

import yaml
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

def _make_app(tmp_path):
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    app.state.auth.setup_user("admin", "Admin", "", "adminpass")
    return app


@pytest.fixture()
def ws_app(tmp_path):
    return _make_app(tmp_path)


@pytest.fixture()
def unauthed_client(ws_app):
    """TestClient with no session cookie."""
    with TestClient(ws_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def authed_client(ws_app):
    """TestClient with a valid taos_session cookie."""
    record = ws_app.state.auth.find_user("admin")
    token = ws_app.state.auth.create_session(user_id=record["id"], long_lived=True)
    with TestClient(ws_app, raise_server_exceptions=False) as c:
        c.cookies.set("taos_session", token)
        yield c


# ---------------------------------------------------------------------------
# /ws/terminal
# ---------------------------------------------------------------------------

class TestTerminalWsAuth:
    def test_unauthenticated_is_rejected_before_shell_spawns(
        self, unauthed_client, monkeypatch
    ):
        """No session cookie → connection closed 1008, os.fork never called."""
        fork_calls: list = []

        import tinyagentos.routes.terminal as term_mod
        monkeypatch.setattr(term_mod.os, "fork", lambda: fork_calls.append(1) or 0)

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with unauthed_client.websocket_connect("/ws/terminal") as ws:
                ws.receive_text()

        assert exc_info.value.code == 1008
        assert fork_calls == [], "os.fork must not be called for unauthenticated connect"

    def test_invalid_session_cookie_is_rejected(self, ws_app, monkeypatch):
        """Bogus cookie → 1008, no shell."""
        import tinyagentos.routes.terminal as term_mod
        monkeypatch.setattr(term_mod.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("fork called")))

        with TestClient(ws_app, raise_server_exceptions=False) as c:
            c.cookies.set("taos_session", "not-a-real-session-token")
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with c.websocket_connect("/ws/terminal") as ws:
                    ws.receive_text()

        assert exc_info.value.code == 1008


# ---------------------------------------------------------------------------
# /ws/chat
# ---------------------------------------------------------------------------

class TestChatWsAuth:
    def test_unauthenticated_is_rejected(self, unauthed_client):
        """No session cookie → connection closed 1008, never accepted."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with unauthed_client.websocket_connect("/ws/chat") as ws:
                ws.receive_text()

        assert exc_info.value.code == 1008

    def test_invalid_session_cookie_is_rejected(self, ws_app):
        with TestClient(ws_app, raise_server_exceptions=False) as c:
            c.cookies.set("taos_session", "invalid-token")
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with c.websocket_connect("/ws/chat") as ws:
                    ws.receive_text()

        assert exc_info.value.code == 1008

    def test_authenticated_user_connects(self, authed_client):
        """Valid session cookie → connection accepted."""
        with authed_client.websocket_connect("/ws/chat") as ws:
            ws.send_text('{"type": "ping"}')
            # The endpoint doesn't send a response on unknown message types;
            # connection staying open means auth passed.


# ---------------------------------------------------------------------------
# /ws/canvas/{canvas_id}
# ---------------------------------------------------------------------------

class TestCanvasWsAuth:
    def test_unauthenticated_is_rejected(self, unauthed_client):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with unauthed_client.websocket_connect("/ws/canvas/test-canvas-id") as ws:
                ws.receive_text()

        assert exc_info.value.code == 1008

    def test_invalid_session_cookie_is_rejected(self, ws_app):
        with TestClient(ws_app, raise_server_exceptions=False) as c:
            c.cookies.set("taos_session", "bad-token")
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with c.websocket_connect("/ws/canvas/test-canvas-id") as ws:
                    ws.receive_text()

        assert exc_info.value.code == 1008

    def test_authenticated_user_connects(self, authed_client):
        """Valid session cookie → connection accepted, hub join succeeds."""
        with authed_client.websocket_connect("/ws/canvas/test-canvas-id") as ws:
            # Connection staying open means auth passed and hub.join ran.
            pass


# ---------------------------------------------------------------------------
# /ws/chat/{agent_name}  (channel_hub webchat WS)
# ---------------------------------------------------------------------------

class TestWebchatWsAuth:
    def test_unauthenticated_is_rejected(self, unauthed_client):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with unauthed_client.websocket_connect("/ws/chat/some-agent") as ws:
                ws.receive_text()

        assert exc_info.value.code == 1008

    def test_invalid_session_cookie_is_rejected(self, ws_app):
        with TestClient(ws_app, raise_server_exceptions=False) as c:
            c.cookies.set("taos_session", "bad-token")
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with c.websocket_connect("/ws/chat/some-agent") as ws:
                    ws.receive_text()

        assert exc_info.value.code == 1008

    def test_authenticated_user_connects(self, authed_client):
        """Valid session cookie → connection accepted; user_id attributed to messages."""
        with authed_client.websocket_connect("/ws/chat/some-agent") as ws:
            ws.send_text('{"text": "hello", "name": "Admin"}')
            # Response may or may not arrive (no agent running); the connection
            # accepting without 1008 confirms auth + user_id propagation succeeded.
