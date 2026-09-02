"""Tests for the agent state versioning routes."""
from __future__ import annotations

import os
import subprocess

import pytest
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks
from unittest.mock import AsyncMock, patch

import importlib.util
import yaml


def _init_fixture_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "agent@taos.local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test-agent"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("initial")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    (path / "notes.txt").write_text("second commit")
    subprocess.run(["git", "add", "notes.txt"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add notes"], cwd=path, check=True, capture_output=True)


def _fake_exec_for_repo(fixture_repo):
    async def _fake(container, cmd, timeout=60):
        if cmd[0] == "git" and cmd[1] == "-C" and cmd[2] == "/root":
            git_args = cmd[3:]
            result = subprocess.run(
                ["git", "-C", str(fixture_repo), *git_args],
                capture_output=True,
                text=True,
            )
            return result.returncode, result.stdout
        return 0, ""
    return _fake


def _make_app_with_remote(tmp_path, remote):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {"name": "test-agent", "host": "192.168.1.100", "remote": remote, "qmd_index": "test", "color": "#98fb98"}
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    app = create_app(data_dir=tmp_path)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True
    return app, token


@pytest.mark.asyncio
class TestAgentVersionsRoutes:
    async def test_list_versions_returns_commits(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(0, "abc123|initial|agent|agent@taos.local|2026-01-01 00:00:00 +0000\ndef456|add notes|agent|agent@taos.local|2026-01-01 01:00:00 +0000\n")),
        ):
            resp = await client.get("/api/agents/test-agent/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "test-agent"
        assert len(data["versions"]) == 2
        assert data["versions"][0]["sha"] == "abc123"

    async def test_list_versions_unknown_agent_returns_404(self, client):
        resp = await client.get("/api/agents/ghost-agent/versions")
        assert resp.status_code == 404

    async def test_diff_returns_patch(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(0, "diff --git a/README.md b/README.md\n")),
        ):
            resp = await client.get("/api/agents/test-agent/versions/abc123/diff")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sha"] == "abc123"
        assert "diff --git" in data["diff"]

    async def test_diff_unknown_sha_returns_404(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(side_effect=RuntimeError("unknown revision")),
        ):
            resp = await client.get("/api/agents/test-agent/versions/abcd1234/diff")
        assert resp.status_code == 404

    async def test_diff_injection_sha_returns_400(self, client):
        resp = await client.get("/api/agents/test-agent/versions/--output=.bashrc/diff")
        assert resp.status_code == 400

    async def test_revert_restores_content(self, tmp_path, client):
        fixture = tmp_path / "repo"
        fixture.mkdir()
        _init_fixture_repo(fixture)

        first_sha = subprocess.run(
            ["git", "-C", str(fixture), "rev-parse", "HEAD~1"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=_fake_exec_for_repo(fixture),
        ):
            resp = await client.post(f"/api/agents/test-agent/versions/{first_sha}/revert")
        print("RESP:", resp.status_code, resp.text)
        assert resp.status_code == 200
        assert resp.json()["status"] == "reverted"
        assert (fixture / "README.md").exists()
        assert not (fixture / "notes.txt").exists()

    async def test_revert_unknown_sha_returns_404(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(side_effect=RuntimeError("unknown revision")),
        ):
            resp = await client.post("/api/agents/test-agent/versions/abcd1234/revert")
        assert resp.status_code == 404

    async def test_revert_injection_sha_returns_400(self, client):
        resp = await client.post("/api/agents/test-agent/versions/--output=.bashrc/revert")
        assert resp.status_code == 400

    async def test_remote_agent_uses_qualified_container_name(self, tmp_path):
        app, token = _make_app_with_remote(tmp_path, "test-remote")
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": token},
            event_hooks=csrf_event_hooks(),
        ) as c:
            with patch(
                "tinyagentos.agent_git.exec_in_container",
                new=AsyncMock(return_value=(0, "abc123|initial|agent|agent@taos.local|2026-01-01 00:00:00 +0000\n")),
            ) as m:
                resp = await c.get("/api/agents/test-agent/versions")
                m.assert_called_once()
                args, _ = m.call_args
                assert args[0] == "test-remote:taos-agent-test-agent"
        assert resp.status_code == 200

    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.get("/api/agents/test-agent/versions")
        assert resp.status_code in (401, 403)
