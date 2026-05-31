"""Tests for the container shell route (issue #462)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ── GET /api/container-shell/{agent_id} (HTML page) ─────────────────────────


class TestContainerShellPage:
    """Tests for the container shell HTML page endpoint."""

    def test_page_returns_html(self, test_client):
        """GET /api/container-shell/{agent_id} returns HTML with correct content-type."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_page_contains_container_name(self, test_client):
        """The page must show the container name derived from the agent id."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert "taos-agent-test-agent-1" in resp.text

    def test_page_escapes_agent_id(self, test_client):
        """HTML-unsafe characters in agent_id must be escaped."""
        resp = test_client.get("/api/container-shell/test<script>alert(1)</script>")
        assert resp.status_code == 200
        body = resp.text
        assert "<script>alert" not in body
        assert "&lt;script&gt;" in body

    def test_page_has_pico_css_reference(self, test_client):
        """The page must reference Pico CSS for styling."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert "pico.min.css" in resp.text

    def test_page_has_shell_input(self, test_client):
        """The page must include a command input field."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert 'id="shell-cmd"' in resp.text
        assert 'type="text"' in resp.text

    def test_page_has_output_region(self, test_client):
        """The page must include an output / log region."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert 'id="output"' in resp.text

    def test_page_has_aria_labels(self, test_client):
        """Interactive elements must have ARIA labels."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        body = resp.text
        assert 'aria-label="Shell command"' in body
        assert 'aria-label="Terminal output"' in body
        assert 'aria-label="Run command"' in body

    def test_page_has_aria_live_region(self, test_client):
        """The output area must be an ARIA live region for screen readers."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert 'aria-live="polite"' in resp.text
        assert 'role="log"' in resp.text

    def test_page_references_htmx(self, test_client):
        """The page must load htmx for AJAX command submission."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert "htmx" in resp.text

    def test_page_has_run_button(self, test_client):
        """The page must include a submit button."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert 'id="shell-btn"' in resp.text

    def test_page_hx_post_targets_exec_endpoint(self, test_client):
        """The form must POST to the correct exec endpoint."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert "/api/container-shell/test-agent-1/exec" in resp.text

    def test_page_mentions_container_shell_ready(self, test_client):
        """The page should indicate that the container shell is ready."""
        resp = test_client.get("/api/container-shell/test-agent-1")
        assert resp.status_code == 200
        assert "Container shell" in resp.text


# ── POST /api/container-shell/{agent_id}/exec (command execution) ───────────


class TestContainerShellExec:
    """Tests for the command execution endpoint."""

    def test_exec_rejects_empty_command(self, test_client):
        """Empty command must be rejected with an info message."""
        resp = test_client.post(
            "/api/container-shell/test-agent/exec",
            data={"command": ""},
        )
        assert resp.status_code == 200
        assert "empty command" in resp.text

    def test_exec_rejects_too_long_command(self, test_client):
        """Commands exceeding the max length must be rejected."""
        long_cmd = "x" * 5000
        resp = test_client.post(
            "/api/container-shell/test-agent/exec",
            data={"command": long_cmd},
        )
        assert resp.status_code == 200
        assert "too long" in resp.text.lower()

    def test_exec_runs_command_and_returns_output(self, test_client):
        """A valid command must execute via incus exec and return HTML output."""
        import asyncio as _asyncio
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello world\n", b""))
        mock_proc.returncode = 0

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "echo hello"},
            )
            assert resp.status_code == 200

        # Verify incus exec was called with the correct container name pattern
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "incus"
        assert call_args[1] == "exec"
        assert call_args[2] == "taos-agent-test-agent"
        assert call_args[3] == "--"
        assert call_args[4] == "bash"
        assert call_args[5] == "-lc"
        assert call_args[6] == "echo hello"

    def test_exec_returns_escaped_html_output(self, test_client):
        """Output containing HTML must be escaped."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"<script>alert(1)</script>\n", b""))
        mock_proc.returncode = 0

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "echo '<script>'"},
            )
            assert resp.status_code == 200
            body = resp.text
            assert "&lt;script&gt;" in body
            assert "<script>" not in body  # raw tags must not appear

    def test_exec_strips_ansi_escape_sequences(self, test_client):
        """Terminal ANSI escape codes must be stripped from output."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"\x1b[32mgreen text\x1b[0m\n", b"")
        )
        mock_proc.returncode = 0

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "ls"},
            )
            assert resp.status_code == 200
            assert "\x1b[32m" not in resp.text
            assert "green text" in resp.text

    def test_exec_returns_html_fragment(self, test_client):
        """The exec response must be an HTML fragment with command output classes."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output\n", b""))
        mock_proc.returncode = 0

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "ls"},
            )
            assert resp.status_code == 200
            assert "cmd-line" in resp.text
            assert "cmd-out" in resp.text

    def test_exec_shows_command_in_output(self, test_client):
        """The executed command must be shown in the returned HTML."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"result\n", b""))
        mock_proc.returncode = 0

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "ls -la /tmp"},
            )
            assert resp.status_code == 200
            assert "ls -la /tmp" in resp.text

    def test_exec_handles_incus_not_found(self, test_client):
        """When incus is not installed, a helpful error message is returned."""
        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("incus not found"),
        ):
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "ls"},
            )
            assert resp.status_code == 200
            assert "incus: command not found" in resp.text

    def test_exec_handles_nonzero_exit_code(self, test_client):
        """Commands that fail (non-zero exit) must still return output."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"bash: line 1: nosuchcmd: command not found\n", b"")
        )
        mock_proc.returncode = 127

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "nosuchcmd"},
            )
            assert resp.status_code == 200
            assert "command not found" in resp.text

    def test_exec_uses_correct_container_naming_scheme(self, test_client):
        """Container name must follow the taos-agent-{id} pattern."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
        mock_proc.returncode = 0

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            test_client.post(
                "/api/container-shell/some-agent-id/exec",
                data={"command": "whoami"},
            )

        call_args = mock_exec.call_args[0]
        assert call_args[2] == "taos-agent-some-agent-id"

    def test_exec_renders_empty_output_gracefully(self, test_client):
        """Commands with no stdout must show a placeholder."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch(
            "tinyagentos.routes.container_shell.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            resp = test_client.post(
                "/api/container-shell/test-agent/exec",
                data={"command": "true"},
            )
            assert resp.status_code == 200
            assert "(no output)" in resp.text
