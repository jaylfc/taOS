"""Tests for #617 — tmux-backed persistent agent terminals.

Two surfaces:
  * the shortcut terminal WebSocket must spawn its PTY through
    ``tmux new-session -A -s taos-term-<agent>-<kind>-<idx>`` so closing the
    socket detaches instead of killing the shell;
  * the lifecycle routes (GET/POST/DELETE /api/agents/{name}/terminals...)
    list, restart and kill those sessions inside the container.
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
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "agent_id": agent_id,
        "shortcut_idx": idx,
        "scope": scope,
        "expires_at": time.monotonic() + 300,
    }
    return session_id


class FakePtyHandle:
    def read(self, size: int = 4096) -> bytes:
        return b"$ "

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        pass


def _connect_and_capture(test_client, agent_id: str, idx: int, session_id: str):
    """Open the terminal WS, do the ticket handshake, return captured spawn args."""
    captured: list = []

    def _fake_get_pty(agent_name: str, cmd):
        captured.append((agent_name, cmd))
        return FakePtyHandle()

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
                f"/shortcut/terminal/{agent_id}/{idx}"
            ) as ws:
                ws.send_text(json.dumps({"type": "ticket", "ticket": "dummy-ticket"}))
                ws.receive_text()
        finally:
            test_client.cookies.clear()
    return captured


# ---------------------------------------------------------------------------
# Session naming
# ---------------------------------------------------------------------------

class TestSessionNaming:
    def test_name_shape(self):
        from tinyagentos.routes.shortcut_proxy import terminal_session_name

        assert (
            terminal_session_name("agent1", "container-terminal", 0)
            == "taos-term-agent1-container-terminal-0"
        )
        assert terminal_session_name("agent1", "tui", 2) == "taos-term-agent1-tui-2"

    def test_name_sanitises_tmux_target_separators(self):
        from tinyagentos.routes.shortcut_proxy import terminal_session_name

        # '.' and ':' are tmux target separators and must not survive into a
        # session name.
        name = terminal_session_name("my.agent:1", "tui", 0)
        assert "." not in name and ":" not in name
        assert name.startswith("taos-term-")

    def test_parse_round_trip_and_foreign_names(self):
        from tinyagentos.routes.shortcut_proxy import (
            parse_terminal_session,
            terminal_session_name,
        )

        name = terminal_session_name("agent1", "container-terminal", 3)
        assert parse_terminal_session("agent1", name) == ("container-terminal", 3)
        # Another agent's session, a non-terminal tmux session, and junk all
        # fail to parse for this agent.
        assert parse_terminal_session("other", name) is None
        assert parse_terminal_session("agent1", "some-random-session") is None
        assert parse_terminal_session("agent1", "taos-term-agent1-tui-x") is None


# ---------------------------------------------------------------------------
# WebSocket spawn wrap (fail-first: on pre-#617 code the spawn passes
# cmd=None for container-terminal and ['bash','-lc',...] for tui)
# ---------------------------------------------------------------------------

class TestTerminalWsTmuxWrap:
    def test_container_terminal_spawns_tmux_attach_or_create(
        self, test_client, seeded_agent_factory
    ):
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

        captured = _connect_and_capture(test_client, agent_id, 0, session_id)

        assert captured == [
            (
                agent_id,
                [
                    "tmux",
                    "new-session",
                    "-A",
                    "-s",
                    f"taos-term-{agent_id}-container-terminal-0",
                ],
            )
        ]

    def test_tui_spawns_tmux_with_command(self, test_client, seeded_agent_factory):
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

        captured = _connect_and_capture(test_client, agent_id, 0, session_id)

        assert captured == [
            (
                agent_id,
                [
                    "tmux",
                    "new-session",
                    "-A",
                    "-s",
                    f"taos-term-{agent_id}-tui-0",
                    "bash -lc htop",
                ],
            )
        ]


# ---------------------------------------------------------------------------
# Lifecycle routes — fake container exec with canned tmux output
# ---------------------------------------------------------------------------

class FakeExec:
    """Async stand-in for containers.exec_in_container driving canned tmux."""

    def __init__(self, sessions: dict[str, int] | None = None):
        # session name -> created epoch
        self.sessions: dict[str, int] = dict(sessions or {})
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, container: str, cmd: list[str], timeout: int = 300):
        self.calls.append((container, list(cmd)))
        if cmd[:2] == ["tmux", "list-sessions"]:
            if not self.sessions:
                return (1, "no server running on /tmp/tmux-1000/default")
            out = "".join(
                f"{name}\t{created}\n" for name, created in self.sessions.items()
            )
            return (0, out)
        if cmd[:2] == ["tmux", "kill-session"]:
            target = cmd[cmd.index("-t") + 1]
            if target.startswith("="):
                # tmux '=' target form: exact match only.
                exact = target[1:]
                if exact in self.sessions:
                    del self.sessions[exact]
                    return (0, "")
                return (1, f"can't find session: {exact}")
            if target in self.sessions:
                del self.sessions[target]
                return (0, "")
            # Real tmux also accepts a unique session-name PREFIX as a
            # target — the behaviour the routes must not rely on.
            prefixed = [s for s in self.sessions if s.startswith(target)]
            if len(prefixed) == 1:
                del self.sessions[prefixed[0]]
                return (0, "")
            return (1, f"can't find session: {target}")
        if cmd[:2] == ["tmux", "new-session"]:
            name = cmd[cmd.index("-s") + 1]
            self.sessions[name] = 1700000000
            return (0, "")
        return (0, "")


@pytest.fixture
def fake_exec(monkeypatch):
    fake = FakeExec()
    monkeypatch.setattr(
        "tinyagentos.routes.agent_terminals.exec_in_container", fake
    )
    return fake


_TERMINAL_SHORTCUTS = [
    {
        "kind": "container-terminal",
        "label": "Shell",
        "icon": "terminal",
        "requires_capability": "agent.shell",
    },
    {
        "kind": "tui",
        "label": "htop",
        "icon": "terminal",
        "requires_capability": "agent.shell",
        "command": "htop",
    },
]


class TestListTerminals:
    def test_lists_only_this_agents_sessions_with_kind_and_started_at(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        fake_exec.sessions = {
            f"taos-term-{name}-container-terminal-0": 1700000000,
            f"taos-term-{name}-tui-1": 1700000100,
            "taos-term-someone-else-tui-0": 1700000200,
            "unrelated-session": 1700000300,
        }

        resp = test_client.get(
            f"/api/agents/{name}/terminals", headers=admin_auth_headers
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["agent"] == name
        terminals = {t["name"]: t for t in body["terminals"]}
        assert set(terminals) == {
            f"taos-term-{name}-container-terminal-0",
            f"taos-term-{name}-tui-1",
        }
        shell = terminals[f"taos-term-{name}-container-terminal-0"]
        assert shell["kind"] == "container-terminal"
        # 1700000000 == 2023-11-14T22:13:20Z
        assert shell["started_at"] == "2023-11-14T22:13:20+00:00"
        assert terminals[f"taos-term-{name}-tui-1"]["kind"] == "tui"
        # The exec went to this agent's container.
        assert fake_exec.calls[0][0] == f"taos-agent-{name}"
        assert fake_exec.calls[0][1][:2] == ["tmux", "list-sessions"]

    def test_no_tmux_server_is_an_empty_list(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        resp = test_client.get(
            f"/api/agents/{agent['name']}/terminals", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["terminals"] == []

    def test_unknown_agent_404(
        self, test_client, admin_auth_headers, fake_exec
    ):
        resp = test_client.get(
            "/api/agents/no-such-agent/terminals", headers=admin_auth_headers
        )
        assert resp.status_code == 404
        assert fake_exec.calls == []

    def test_unauthenticated_rejected(self, test_client, seeded_agent_factory, fake_exec):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        resp = test_client.get(f"/api/agents/{agent['name']}/terminals")
        assert resp.status_code in (401, 403)


class TestKillTerminal:
    def test_kill_existing_session(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        session = f"taos-term-{name}-container-terminal-0"
        fake_exec.sessions = {session: 1700000000}

        resp = test_client.delete(
            f"/api/agents/{name}/terminals/{session}", headers=admin_auth_headers
        )

        assert resp.status_code == 200
        assert session not in fake_exec.sessions
        assert fake_exec.calls == [
            (f"taos-agent-{name}", ["tmux", "kill-session", "-t", f"={session}"])
        ]

    def test_kill_missing_session_404(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        session = f"taos-term-{name}-tui-1"
        resp = test_client.delete(
            f"/api/agents/{name}/terminals/{session}", headers=admin_auth_headers
        )
        assert resp.status_code == 404

    def test_kill_absent_prefix_of_existing_session_404(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        """tmux targets accept unique prefixes: killing absent slot 1 must not
        destroy the existing slot 10 whose name it prefixes."""
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        slot10 = f"taos-term-{name}-tui-10"
        slot1 = f"taos-term-{name}-tui-1"
        fake_exec.sessions = {slot10: 1700000000}

        resp = test_client.delete(
            f"/api/agents/{name}/terminals/{slot1}", headers=admin_auth_headers
        )

        assert resp.status_code == 404
        assert slot10 in fake_exec.sessions

    def test_kill_foreign_session_404_without_exec(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        """A session name belonging to another agent must not reach tmux."""
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        foreign = "taos-term-someone-else-tui-0"
        fake_exec.sessions = {foreign: 1700000000}

        resp = test_client.delete(
            f"/api/agents/{name}/terminals/{foreign}", headers=admin_auth_headers
        )

        assert resp.status_code == 404
        assert fake_exec.calls == []
        assert foreign in fake_exec.sessions


class TestRestartTerminal:
    def test_restart_kills_and_recreates_detached(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        session = f"taos-term-{name}-tui-1"
        fake_exec.sessions = {session: 1700000000}

        resp = test_client.post(
            f"/api/agents/{name}/terminals/{session}/restart",
            headers=admin_auth_headers,
        )

        assert resp.status_code == 200
        assert session in fake_exec.sessions  # recreated
        cmds = [cmd for _container, cmd in fake_exec.calls]
        assert cmds[0] == ["tmux", "kill-session", "-t", f"={session}"]
        assert cmds[1] == [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session,
            "bash -lc htop",
        ]

    def test_restart_container_terminal_recreates_bare_session(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        session = f"taos-term-{name}-container-terminal-0"
        fake_exec.sessions = {session: 1700000000}

        resp = test_client.post(
            f"/api/agents/{name}/terminals/{session}/restart",
            headers=admin_auth_headers,
        )

        assert resp.status_code == 200
        cmds = [cmd for _container, cmd in fake_exec.calls]
        assert cmds[1] == ["tmux", "new-session", "-d", "-s", session]

    def test_restart_missing_session_404_and_no_recreate(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        session = f"taos-term-{name}-tui-1"

        resp = test_client.post(
            f"/api/agents/{name}/terminals/{session}/restart",
            headers=admin_auth_headers,
        )

        assert resp.status_code == 404
        cmds = [cmd for _container, cmd in fake_exec.calls]
        assert all(cmd[:2] != ["tmux", "new-session"] for cmd in cmds)

    def test_restart_absent_prefix_of_existing_session_404(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        """Restart shares the kill helper: absent slot 1 must not destroy
        slot 10 via tmux prefix matching."""
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        slot10 = f"taos-term-{name}-tui-10"
        slot1 = f"taos-term-{name}-tui-1"
        fake_exec.sessions = {slot10: 1700000000}

        resp = test_client.post(
            f"/api/agents/{name}/terminals/{slot1}/restart",
            headers=admin_auth_headers,
        )

        assert resp.status_code == 404
        assert slot10 in fake_exec.sessions
        cmds = [cmd for _container, cmd in fake_exec.calls]
        assert all(cmd[:2] != ["tmux", "new-session"] for cmd in cmds)

    def test_restart_unknown_shortcut_idx_404(
        self, test_client, admin_auth_headers, seeded_agent_factory, fake_exec
    ):
        agent = seeded_agent_factory(framework="openclaw", shortcuts=_TERMINAL_SHORTCUTS)
        name = agent["name"]
        session = f"taos-term-{name}-tui-9"
        fake_exec.sessions = {session: 1700000000}

        resp = test_client.post(
            f"/api/agents/{name}/terminals/{session}/restart",
            headers=admin_auth_headers,
        )

        assert resp.status_code == 404
        assert fake_exec.calls == []
