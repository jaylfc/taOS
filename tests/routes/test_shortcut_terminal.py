"""Tests for the shortcut terminal WebSocket route (Tasks 20-21).

Task 20: PTY bridge — cookie validation, scope check, basic data flow.
Task 21: tui shortcut spawns PTY with bash -lc <command> (since #617, wrapped
in the tmux attach-or-create session command).
"""
from __future__ import annotations

import json
import secrets
import time
from unittest.mock import AsyncMock, patch

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
        ), patch(
            "tinyagentos.routes.shortcut_proxy._tmux_available",
            new=AsyncMock(return_value=True),
        ):
            test_client.cookies.set("taos_shortcut", session_id)
            try:
                with test_client.websocket_connect(
                    f"/shortcut/terminal/{agent_id}/0"
                ) as ws:
                    # Send the required ticket handshake frame (Task 29 protocol).
                    ws.send_text(json.dumps({"type": "ticket", "ticket": "dummy-ticket"}))
                    data = ws.receive_text()
                    assert data == "$ "
            finally:
                test_client.cookies.clear()


# ---------------------------------------------------------------------------
# Task 21 — tui shortcut spawns PTY with bash -lc <command>
# ---------------------------------------------------------------------------

class TestTuiSpawnCommand:
    def test_tui_shortcut_passes_command_to_spawn_pty(
        self, test_client, seeded_agent_factory
    ):
        """For a tui shortcut, spawn_pty gets the tmux attach-or-create wrap (#617)
        carrying the shortcut's command: ['tmux','new-session','-A','-s',<session>,
        'bash -lc <command>']."""
        shortcuts = [
            {
                "kind": "tui",
                "label": "htop",
                "icon": "terminal",
                "requires_capability": "agent.shell",
                "command": "htop",
            }
        ]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        agent_id = agent["id"]
        session_id = _make_terminal_session(agent_id, 0, scope="tui")

        captured_cmd: list = []
        fake_pty = FakePtyHandle()

        def _fake_get_pty(agent_name: str, cmd: list | None) -> FakePtyHandle:
            captured_cmd.extend(cmd or [])
            return fake_pty

        with patch(
            "tinyagentos.routes.shortcut_proxy._get_container_pty",
            side_effect=_fake_get_pty,
        ), patch(
            "tinyagentos.routes.shortcut_proxy._tmux_available",
            new=AsyncMock(return_value=True),
        ):
            test_client.cookies.set("taos_shortcut", session_id)
            try:
                with test_client.websocket_connect(
                    f"/shortcut/terminal/{agent_id}/0"
                ) as ws:
                    # Send the required ticket handshake frame before data.
                    ws.send_text(json.dumps({"type": "ticket", "ticket": "dummy-ticket"}))
                    ws.receive_text()
            finally:
                test_client.cookies.clear()

        assert captured_cmd == [
            "tmux",
            "new-session",
            "-A",
            "-s",
            f"taos-term-{agent_id}-tui-0",
            "bash -lc htop",
        ]


# ---------------------------------------------------------------------------
# C1 — ticket handshake validation (defense-in-depth)
# ---------------------------------------------------------------------------

class TestTerminalHandshake:
    def test_missing_handshake_closes_1008(self, test_client, seeded_agent_factory):
        """Connection that never sends a ticket handshake must be rejected."""
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
                with pytest.raises(Exception):
                    with test_client.websocket_connect(
                        f"/shortcut/terminal/{agent_id}/0"
                    ) as ws:
                        # Send wrong handshake — no "ticket" key
                        ws.send_text(json.dumps({"type": "not-ticket"}))
                        ws.receive_text()
            finally:
                test_client.cookies.clear()

    def test_malformed_json_handshake_closes_1008(self, test_client, seeded_agent_factory):
        """Non-JSON first frame must cause the connection to be rejected."""
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
                with pytest.raises(Exception):
                    with test_client.websocket_connect(
                        f"/shortcut/terminal/{agent_id}/0"
                    ) as ws:
                        ws.send_text("not-json-at-all!!!")
                        ws.receive_text()
            finally:
                test_client.cookies.clear()


# ---------------------------------------------------------------------------
# #617 follow-up — tmux absent from the container image: fall back to the
# plain (pre-#617) spawn with a one-line notice; never fail to open.
# ---------------------------------------------------------------------------

class TestTmuxAbsentFallback:
    def test_plain_terminal_opens_with_notice_when_tmux_absent(
        self, test_client, seeded_agent_factory
    ):
        """No tmux in the container: a container-terminal still opens with
        the plain spawn (cmd=None) and the client first receives the
        one-line won't-persist notice."""
        from tinyagentos.routes.shortcut_proxy import TMUX_ABSENT_NOTICE

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

        captured: dict = {}
        fake_pty = FakePtyHandle()

        def _fake_get_pty(agent_name: str, cmd: list | None) -> FakePtyHandle:
            captured["cmd"] = cmd
            return fake_pty

        with patch(
            "tinyagentos.routes.shortcut_proxy._get_container_pty",
            side_effect=_fake_get_pty,
        ), patch(
            "tinyagentos.routes.shortcut_proxy._tmux_available",
            new=AsyncMock(return_value=False),
        ):
            test_client.cookies.set("taos_shortcut", session_id)
            try:
                with test_client.websocket_connect(
                    f"/shortcut/terminal/{agent_id}/0"
                ) as ws:
                    ws.send_text(json.dumps({"type": "ticket", "ticket": "dummy-ticket"}))
                    first = ws.receive_text()
                    assert first == TMUX_ABSENT_NOTICE
                    assert ws.receive_text() == "$ "
            finally:
                test_client.cookies.clear()

        assert captured["cmd"] is None

    def test_tui_falls_back_to_plain_command_when_tmux_absent(
        self, test_client, seeded_agent_factory
    ):
        """No tmux in the container: a tui shortcut spawns its plain
        ['bash','-lc',<command>] (the pre-#617 argv) and shows the notice."""
        from tinyagentos.routes.shortcut_proxy import TMUX_ABSENT_NOTICE

        shortcuts = [
            {
                "kind": "tui",
                "label": "htop",
                "icon": "terminal",
                "requires_capability": "agent.shell",
                "command": "htop",
            }
        ]
        agent = seeded_agent_factory(framework="openclaw", shortcuts=shortcuts)
        agent_id = agent["id"]
        session_id = _make_terminal_session(agent_id, 0, scope="tui")

        captured: dict = {}
        fake_pty = FakePtyHandle()

        def _fake_get_pty(agent_name: str, cmd: list | None) -> FakePtyHandle:
            captured["cmd"] = cmd
            return fake_pty

        with patch(
            "tinyagentos.routes.shortcut_proxy._get_container_pty",
            side_effect=_fake_get_pty,
        ), patch(
            "tinyagentos.routes.shortcut_proxy._tmux_available",
            new=AsyncMock(return_value=False),
        ):
            test_client.cookies.set("taos_shortcut", session_id)
            try:
                with test_client.websocket_connect(
                    f"/shortcut/terminal/{agent_id}/0"
                ) as ws:
                    ws.send_text(json.dumps({"type": "ticket", "ticket": "dummy-ticket"}))
                    assert ws.receive_text() == TMUX_ABSENT_NOTICE
            finally:
                test_client.cookies.clear()

        assert captured["cmd"] == ["bash", "-lc", "htop"]


# ---------------------------------------------------------------------------
# Review on #4575 (CodeRabbit, 2026-11-26) — two Majors on the fallback:
# the tmux probe must go through the ACTIVE backend, not the module-level
# Incus exec; and a client disconnect must close the PTY even when the
# shell is quiet (pty_to_ws has no exit condition on empty reads).
# ---------------------------------------------------------------------------

class TestTmuxProbeUsesActiveBackend:
    @pytest.mark.asyncio
    async def test_probe_uses_active_backend_not_module_level_incus(self):
        """With a non-Incus backend active, _tmux_available must ask THAT
        backend whether tmux exists in the container. The module-level
        exec_in_container is hard-coded to `incus exec`: on a Docker/Apple
        host it fails, so the probe would wrongly report tmux absent and
        degrade every terminal to the non-persistent fallback."""
        from tinyagentos.routes.shortcut_proxy import _tmux_available

        backend_exec = AsyncMock(return_value=(0, ""))
        fake_backend = type("FakeBackend", (), {"exec_in_container": backend_exec})()
        module_exec = AsyncMock(return_value=(1, "incus: not found"))

        with patch(
            "tinyagentos.containers.backend.get_backend", return_value=fake_backend
        ), patch("tinyagentos.containers.exec_in_container", module_exec):
            assert await _tmux_available("agent1") is True

        backend_exec.assert_awaited_once()
        args = backend_exec.await_args.args
        assert args[0] == "taos-agent-agent1"
        assert args[1] == ["sh", "-c", "command -v tmux >/dev/null 2>&1"]
        module_exec.assert_not_awaited()


class QuietPtyHandle:
    """PTY handle whose shell never prints: read() always returns b''."""

    def __init__(self):
        self.closed = False

    def read(self, size: int = 4096) -> bytes:
        return b""

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class TestTerminalDisconnectClosesPty:
    def test_disconnect_with_quiet_shell_closes_pty(
        self, test_client, seeded_agent_factory
    ):
        """Client disconnects while the shell is quiet: ws_to_pty returns,
        and the handler must stop waiting on pty_to_ws and run its cleanup
        (pty_handle.close()). With a bare asyncio.gather the reader loops
        on b'' forever, gather never returns, and the PTY leaks."""
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
        fake_pty = QuietPtyHandle()

        with patch(
            "tinyagentos.routes.shortcut_proxy._get_container_pty",
            return_value=fake_pty,
        ), patch(
            "tinyagentos.routes.shortcut_proxy._tmux_available",
            new=AsyncMock(return_value=True),
        ):
            test_client.cookies.set("taos_shortcut", session_id)
            try:
                with test_client.websocket_connect(
                    f"/shortcut/terminal/{agent_id}/0"
                ) as ws:
                    ws.send_text(json.dumps({"type": "ticket", "ticket": "dummy-ticket"}))
                # Leaving the context disconnects the client.
            finally:
                test_client.cookies.clear()

        assert fake_pty.closed, "PTY handle was not closed after client disconnect"
