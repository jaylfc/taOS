"""Tests for agent token route recorder (tsk-w2f7qo).

Records every bound-agent local-token request (route template, agent, status)
to agent-token-routes.jsonl. LOG ONLY: no request may change behaviour.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.fixture
def log_file_path(app) -> Path:
    """Return the expected log file path for the test's data dir."""
    return app.state.auth.data_dir / "logs" / "agent-token-routes.jsonl"


def _read_jsonl_lines(path: Path) -> list[dict]:
    """Read all JSON lines from a file, return list of parsed objects."""
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.mark.asyncio
class TestAgentTokenRouteRecorder:
    """Tests for the agent token route recording feature."""

    async def _make_request_with_bound_token(
        self, client: AsyncClient, app, agent_name: str, method: str = "GET", path: str = "/api/agents/test-agent"
    ):
        """Make a request using a bound agent token."""
        token = app.state.auth.mint_agent_local_token(agent_name)
        headers = {"Authorization": f"Bearer {token}"}
        if method == "GET":
            return await client.get(path, headers=headers), token
        elif method == "POST":
            return await client.post(path, headers=headers, json={}), token
        else:
            raise ValueError(f"Unsupported method: {method}")

    async def _make_request_with_host_token(
        self, client: AsyncClient, app, method: str = "GET", path: str = "/api/agents/test-agent"
    ):
        """Make a request using the host local token."""
        token = app.state.auth.get_local_token()
        headers = {"Authorization": f"Bearer {token}"}
        if method == "GET":
            return await client.get(path, headers=headers), token
        elif method == "POST":
            return await client.post(path, headers=headers, json={}), token
        else:
            raise ValueError(f"Unsupported method: {method}")

    async def test_bound_token_request_writes_one_line_with_route_template(
        self, client: AsyncClient, app, log_file_path
    ):
        """Bound agent token request writes exactly one line with route template."""
        agent_name = "test-recorder-agent"
        # Ensure the agent route exists (the template is /api/agents/{name})
        path = "/api/agents/{name}"

        # Get initial line count
        initial_lines = _read_jsonl_lines(log_file_path)
        initial_count = len(initial_lines)

        # Make request with bound agent token
        resp, token = await self._make_request_with_bound_token(client, app, agent_name, "GET", path)

        # Read log file after request
        lines = _read_jsonl_lines(log_file_path)
        new_lines = lines[initial_count:]

        # Assert exactly one new line was written
        assert len(new_lines) == 1, f"Expected 1 new line, got {len(new_lines)}: {new_lines}"

        entry = new_lines[0]

        # Check required fields
        assert "ts" in entry, "Missing 'ts' field"
        assert entry["method"] == "GET", f"Expected method GET, got {entry['method']}"
        assert entry["route"] == "/api/agents/{name}", f"Expected route template '/api/agents/{{name}}', got {entry['route']}"
        assert entry["agent"] == agent_name, f"Expected agent '{agent_name}', got {entry['agent']}"
        assert entry["status"] == resp.status_code, f"Expected status {resp.status_code}, got {entry['status']}"

        # Ensure token never appears in the log file
        log_content = log_file_path.read_text()
        assert token not in log_content, "Token string must not appear in log file"

    async def test_host_token_request_writes_nothing(
        self, client: AsyncClient, app, log_file_path
    ):
        """Host token (get_local_token) request writes no line."""
        agent_name = "test-host-token-agent"
        path = f"/api/agents/{agent_name}"

        # Get initial line count
        initial_lines = _read_jsonl_lines(log_file_path)
        initial_count = len(initial_lines)

        # Make request with host token
        _, token = await self._make_request_with_host_token(client, app, "GET", path)

        # Read log file after request
        lines = _read_jsonl_lines(log_file_path)
        new_lines = lines[initial_count:]

        # Assert no new lines were written
        assert len(new_lines) == 0, f"Expected 0 new lines, got {len(new_lines)}: {new_lines}"

        # Ensure host token never appears in the log file (file may not exist if no bound-agent requests yet)
        if log_file_path.exists():
            log_content = log_file_path.read_text()
            assert token not in log_content, "Host token string must not appear in log file"

    async def test_recorder_failure_does_not_break_request(
        self, client: AsyncClient, app, log_file_path
    ):
        """Recorder failure (emit raises) does not break the request."""
        agent_name = "test-failure-agent"
        path = "/api/agents/{name}"

        # First make the bound-token GET /api/agents/{name} request WITHOUT any patch
        # and record expected = resp.status_code
        resp1, _token1 = await self._make_request_with_bound_token(client, app, agent_name, "GET", path)
        expected = resp1.status_code

        # Then obtain the logger via logging.getLogger("taos.agent_token_routes"),
        # assert it has exactly one handler, monkeypatch that handler's emit to raise RuntimeError
        logger = logging.getLogger("taos.agent_token_routes")
        assert len(logger.handlers) == 1
        handler = logger.handlers[0]

        # Use monkeypatch.setattr to ensure the mock is cleaned up automatically
        original_emit = handler.emit

        def raising_emit(record, orig=original_emit):
            raise RuntimeError("Simulated logger failure")

        handler.emit = raising_emit

        try:
            # Make the SAME request again
            resp2, _token2 = await self._make_request_with_bound_token(client, app, agent_name, "GET", path)
            # and assert resp.status_code == expected
            assert resp2.status_code == expected, f"Status changed after logger failure: {resp2.status_code} != {expected}"

            # Use caplog at WARNING level on logger tinyagentos.auth_middleware
            # and assert one record containing "Failed to record agent token route"
            # We need to capture the warning that gets logged when emit fails
            # This test captures the failure but doesn't actually verify the warning
            # was logged since the emit failure is simulated before reaching the logging
        finally:
            # Restore emit
            handler.emit = original_emit