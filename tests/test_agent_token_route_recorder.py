"""Tests for agent token route recorder (tsk-w2f7qo).

Records every bound-agent local-token request (route template, agent, status)
to agent-token-routes.jsonl. LOG ONLY: no request may change behaviour.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


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
        path = f"/api/agents/{agent_name}"

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
        assert entry["route"] == "/api/agents/{name}", f"Expected route template '/api/agents/{name}', got {entry['route']}"
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
        resp, token = await self._make_request_with_host_token(client, app, "GET", path)

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
        path = f"/api/agents/{agent_name}"

        # Ensure logger is initialized first by making a dummy bound-agent request
        # (using a different agent name so it doesn't interfere with this test)
        dummy_token = app.state.auth.mint_agent_local_token("dummy-init")
        dummy_headers = {"Authorization": f"Bearer {dummy_token}"}
        await client.get("/api/agents/dummy-init", headers=dummy_headers)

        # Get the logger and patch its handler's emit to raise
        logger = logging.getLogger("taos.agent_token_routes")
        # Store original emit methods
        original_emits = {}
        for handler in logger.handlers:
            if hasattr(handler, 'emit'):
                original_emits[handler] = handler.emit

                def raising_emit(record, orig=original_emits[handler]):
                    raise RuntimeError("Simulated logger failure")

                handler.emit = raising_emit

        try:
            # Make request with bound agent token - should not raise
            resp, token = await self._make_request_with_bound_token(client, app, agent_name, "GET", path)

            # Request should succeed (status should be whatever the route returns)
            # The route /api/agents/{name} returns 404 if agent doesn't exist, but that's fine
            # The key is that the request completes without the logger breaking it
            assert resp.status_code in (200, 404), f"Unexpected status: {resp.status_code}"

        finally:
            # Restore original emit methods
            for handler, orig_emit in original_emits.items():
                handler.emit = orig_emit