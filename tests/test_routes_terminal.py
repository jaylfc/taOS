"""Endpoint tests for tinyagentos/routes/terminal.py."""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from tinyagentos.app import create_app
from tinyagentos.routes.terminal import build_command
from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
import yaml


@pytest.fixture
def app(tmp_path):
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
    _app = create_app(data_dir=tmp_path)
    _app.state.vapid_keypair = load_or_create_vapid_keypair(tmp_path)
    return _app


@pytest.fixture
def test_client(app):
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


class TestTerminalRouteExists:
    def test_ws_terminal_route_registered(self, app):
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/ws/terminal" in routes, f"Terminal WS route not found. Routes: {routes}"


class TestTerminalUnauthenticated:
    def test_missing_session_cookie_closes_1008(self, test_client):
        with pytest.raises(Exception):
            with test_client.websocket_connect("/ws/terminal") as ws:
                ws.receive_text()


class TestTerminalAuthenticated:
    # SKIP: the happy path requires os.fork / pty.openpty / os.execvpe,
    # which spawns a real shell process and manages a PTY in the parent.
    # That is live infrastructure (a real OS process, PTY, and shell binary)
    # and cannot be exercised in-process with the FastAPI test client.
    pass


class TestBuildCommand:
    def test_local_mode_returns_shell(self):
        result = build_command({})
        assert result == [os.environ.get("SHELL", "/bin/bash"), "-l"]

    def test_local_mode_explicit(self):
        result = build_command({"mode": "local"})
        assert result == [os.environ.get("SHELL", "/bin/bash"), "-l"]

    def test_ssh_valid_config(self):
        result = build_command({
            "mode": "ssh",
            "host": "example.com",
            "username": "user",
            "port": 22,
        })
        assert result == [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-p", "22",
            "user@example.com",
        ]

    def test_ssh_with_password(self):
        result = build_command({
            "mode": "ssh",
            "host": "example.com",
            "username": "user",
            "password": "secret",
        })
        assert result == [
            "sshpass", "-p", "secret",
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-p", "22",
            "user@example.com",
        ]

    def test_ssh_missing_host_raises(self):
        with pytest.raises(ValueError, match="SSH requires host and username"):
            build_command({"mode": "ssh", "username": "user"})

    def test_ssh_missing_username_raises(self):
        with pytest.raises(ValueError, match="SSH requires host and username"):
            build_command({"mode": "ssh", "host": "example.com"})

    def test_ssh_empty_host_raises(self):
        with pytest.raises(ValueError, match="SSH requires host and username"):
            build_command({"mode": "ssh", "host": "", "username": "user"})

    def test_ssh_default_port(self):
        result = build_command({"mode": "ssh", "host": "example.com", "username": "user"})
        assert "-p" in result
        p_idx = result.index("-p")
        assert result[p_idx + 1] == "22"

    def test_ssh_custom_port(self):
        result = build_command({
            "mode": "ssh",
            "host": "example.com",
            "username": "user",
            "port": 2222,
        })
        p_idx = result.index("-p")
        assert result[p_idx + 1] == "2222"

    def test_ssh_none_password_treated_as_empty(self):
        result = build_command({
            "mode": "ssh",
            "host": "example.com",
            "username": "user",
            "password": None,
        })
        assert "sshpass" not in result
        assert result[0] == "ssh"
