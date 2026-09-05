"""Tests for the agent state versioning routes."""
from __future__ import annotations

import os
import subprocess

import pytest
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks
from unittest.mock import AsyncMock, MagicMock, patch

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
        if cmd[0] == "bash" and cmd[1] == "-c":
            script = cmd[2].replace("/root", str(fixture_repo))
            result = subprocess.run(
                [cmd[0], cmd[1], script],
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
            new=AsyncMock(return_value=(0, "abc12345\x1fagent\x1fagent@taos.local\x1f2026-01-01 00:00:00 +0000\x1finitial\x00def456789\x1fagent\x1fagent@taos.local\x1f2026-01-01 01:00:00 +0000\x1fadd notes\x00")),
        ):
            resp = await client.get("/api/agents/test-agent/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "test-agent"
        assert len(data["versions"]) == 2
        assert data["versions"][0]["sha"] == "abc12345"

    async def test_list_versions_unknown_agent_returns_404(self, client):
        resp = await client.get("/api/agents/ghost-agent/versions")
        assert resp.status_code == 404

    async def test_diff_returns_patch(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(0, "diff --git a/README.md b/README.md\n")),
        ):
            resp = await client.get("/api/agents/test-agent/versions/abc12345/diff")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sha"] == "abc12345"
        assert "diff --git" in data["diff"]

    async def test_diff_unknown_sha_returns_404(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(side_effect=RuntimeError("unknown revision")),
        ):
            resp = await client.get("/api/agents/test-agent/versions/abcd12345/diff")
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
                new=AsyncMock(return_value=(0, "abc12345\x1fagent\x1fagent@taos.local\x1f2026-01-01 00:00:00 +0000\x1finitial\n")),
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

    async def test_list_versions_with_pipe_in_subject_parses_all_fields(self, client):
        subject = "auto: 2026-01-01 00:00:00 | added new feature"
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(0, f"abc12345\x1fagent\x1fagent@taos.local\x1f2026-01-01 00:00:00 +0000\x1f{subject}\n")),
        ):
            resp = await client.get("/api/agents/test-agent/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["versions"]) == 1
        assert data["versions"][0]["sha"] == "abc12345"
        assert data["versions"][0]["message"] == subject
        assert data["versions"][0]["author_name"] == "agent"
        assert data["versions"][0]["author_email"] == "agent@taos.local"
        assert data["versions"][0]["date"] == "2026-01-01 00:00:00 +0000"

    async def test_list_versions_message_with_separator_parses_correctly(self, client):
        subject = "fix: handle files with \x1f separator in name"
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(0, f"abc12345\x1fagent\x1fagent@taos.local\x1f2026-01-01 00:00:00 +0000\x1f{subject}\n")),
        ):
            resp = await client.get("/api/agents/test-agent/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["versions"]) == 1
        assert data["versions"][0]["sha"] == "abc12345"
        assert data["versions"][0]["message"] == subject
        assert data["versions"][0]["author_name"] == "agent"
        assert data["versions"][0]["author_email"] == "agent@taos.local"
        assert data["versions"][0]["date"] == "2026-01-01 00:00:00 +0000"

    async def test_revert_to_head_returns_noop(self, tmp_path, client):
        fixture = tmp_path / "repo"
        fixture.mkdir()
        _init_fixture_repo(fixture)
        head_sha = subprocess.run(
            ["git", "-C", str(fixture), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=_fake_exec_for_repo(fixture),
        ):
            resp = await client.post(f"/api/agents/test-agent/versions/{head_sha}/revert")
        assert resp.status_code == 200
        assert resp.json()["status"] == "noop"

    async def test_revert_wins_a_commit_racing_the_sha_resolution(self, tmp_path, client):
        """The auto-committer can commit between resolving the requested sha and
        the reset. The noop decision therefore belongs inside the flock: a sha
        that was HEAD a moment ago must still be restored, not reported `noop`
        while the tree sits on the committer's new commit."""
        fixture = tmp_path / "repo"
        fixture.mkdir()
        _init_fixture_repo(fixture)
        head_sha = subprocess.run(
            ["git", "-C", str(fixture), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        passthrough = _fake_exec_for_repo(fixture)
        raced = []

        async def racing_exec(container, cmd, timeout=60):
            result = await passthrough(container, cmd, timeout)
            if not raced and "rev-parse" in cmd:
                raced.append(cmd)
                (fixture / "racy.txt").write_text("written between resolve and revert")
                subprocess.run(
                    ["git", "-C", str(fixture), "add", "racy.txt"],
                    check=True, capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(fixture), "commit", "-m", "auto: racy"],
                    check=True, capture_output=True,
                )
            return result

        with patch("tinyagentos.agent_git.exec_in_container", new=racing_exec):
            resp = await client.post(f"/api/agents/test-agent/versions/{head_sha}/revert")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reverted"
        final_sha = subprocess.run(
            ["git", "-C", str(fixture), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert final_sha == head_sha
        assert not (fixture / "racy.txt").exists()

    async def test_revert_non_ancestor_returns_409(self, tmp_path, client):
        fixture = tmp_path / "repo"
        fixture.mkdir()
        _init_fixture_repo(fixture)
        orphan = subprocess.run(
            ["git", "-C", str(fixture), "commit-tree", "HEAD^{tree}", "-m", "orphan"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=_fake_exec_for_repo(fixture),
        ):
            resp = await client.post(f"/api/agents/test-agent/versions/{orphan}/revert")
        assert resp.status_code == 409

    async def test_revert_dirty_tree_returns_409(self, tmp_path, client):
        fixture = tmp_path / "repo"
        fixture.mkdir()
        _init_fixture_repo(fixture)
        first_sha = subprocess.run(
            ["git", "-C", str(fixture), "rev-parse", "HEAD~1"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        (fixture / "dirty.txt").write_text("uncommitted")
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=_fake_exec_for_repo(fixture),
        ):
            resp = await client.post(f"/api/agents/test-agent/versions/{first_sha}/revert")
        assert resp.status_code == 409

    async def test_short_sha_rejected_by_versions_route(self, client):
        resp = await client.get("/api/agents/test-agent/versions/abc1/diff")
        assert resp.status_code == 400

    async def test_uppercase_sha_is_accepted(self, tmp_path, client):
        """Hex object names are case-insensitive to git, and every route a
        user copies a sha from can hand one over uppercase."""
        fixture = tmp_path / "repo"
        fixture.mkdir()
        _init_fixture_repo(fixture)
        head_sha = subprocess.run(
            ["git", "-C", str(fixture), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=_fake_exec_for_repo(fixture),
        ):
            resp = await client.post(
                f"/api/agents/test-agent/versions/{head_sha.upper()}/revert"
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "noop"

    async def test_agent_name_that_breaks_the_container_target_returns_400(self, tmp_path):
        """"remote:container" is the qualified form, so a name carrying a
        colon would silently address a different remote."""
        app, token = _make_app_with_remote(tmp_path, None)
        app.state.config.agents[0]["name"] = "bad:name"
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": token},
            event_hooks=csrf_event_hooks(),
        ) as c:
            resp = await c.get("/api/agents/bad:name/versions")
        assert resp.status_code == 400
        assert "invalid agent name" in resp.json()["error"]

    async def test_failed_reset_returns_409_with_the_git_error(self, client):
        """A repo-state failure is 409 and says what git said — not a 404
        "unknown revision" and not a bare "container_unreachable"."""
        async def _fake(container, cmd, timeout=60):
            if cmd[0] == "bash":
                return 1, "fatal: Unable to write new index file"
            if "merge-base" in cmd:
                return 0, ""
            return 0, "b" * 40

        with patch("tinyagentos.agent_git.exec_in_container", new=_fake):
            resp = await client.post(f"/api/agents/test-agent/versions/{'b' * 40}/revert")
        assert resp.status_code == 409
        assert "Unable to write new index file" in resp.json()["error"]

    async def test_list_versions_403_for_unauthorized_user(self, tmp_path):
        config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {"name": "test-agent", "host": "192.168.1.100", "user_id": "owner-user", "qmd_index": "test", "color": "#98fb98"}
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        (tmp_path / ".setup_complete").touch()
        from tinyagentos.app import create_app
        app = create_app(data_dir=tmp_path)
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        admin_record = app.state.auth.find_user("admin")
        invite_code = app.state.auth.add_user_invite("bob", "admin")
        app.state.auth.complete_invite("bob", invite_code, "Bob User", "bob@test.com", "bobpass123")
        bob_record = app.state.auth.find_user("bob")
        bob_token = app.state.auth.create_session(user_id=bob_record["id"], long_lived=True)
        app.state._startup_complete = True
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as c:
            resp = await c.get("/api/agents/test-agent/versions")
        assert resp.status_code == 403

    async def test_revert_403_for_unauthorized_user(self, tmp_path):
        config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {"name": "test-agent", "host": "192.168.1.100", "user_id": "owner-user", "qmd_index": "test", "color": "#98fb98"}
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        (tmp_path / ".setup_complete").touch()
        from tinyagentos.app import create_app
        app = create_app(data_dir=tmp_path)
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        admin_record = app.state.auth.find_user("admin")
        invite_code = app.state.auth.add_user_invite("bob", "admin")
        app.state.auth.complete_invite("bob", invite_code, "Bob User", "bob@test.com", "bobpass123")
        bob_record = app.state.auth.find_user("bob")
        bob_token = app.state.auth.create_session(user_id=bob_record["id"], long_lived=True)
        app.state._startup_complete = True
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as c:
            resp = await c.post("/api/agents/test-agent/versions/abc12345/revert")
        assert resp.status_code == 403

    async def test_list_versions_403_when_ownership_unresolved(self, tmp_path):
        config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {"name": "test-agent", "host": "192.168.1.100", "qmd_index": "test", "color": "#98fb98"}
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        (tmp_path / ".setup_complete").touch()
        from tinyagentos.app import create_app
        app = create_app(data_dir=tmp_path)
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        admin_record = app.state.auth.find_user("admin")
        invite_code = app.state.auth.add_user_invite("bob", "admin")
        app.state.auth.complete_invite("bob", invite_code, "Bob User", "bob@test.com", "bobpass123")
        bob_record = app.state.auth.find_user("bob")
        bob_token = app.state.auth.create_session(user_id=bob_record["id"], long_lived=True)
        app.state._startup_complete = True
        mock_registry = MagicMock()
        mock_registry.get_by_handle = AsyncMock(return_value=None)
        app.state.agent_registry = mock_registry
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as c:
            resp = await c.get("/api/agents/test-agent/versions")
        assert resp.status_code == 403

    async def test_diff_403_when_ownership_unresolved(self, tmp_path):
        config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {"name": "test-agent", "host": "192.168.1.100", "qmd_index": "test", "color": "#98fb98"}
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        (tmp_path / ".setup_complete").touch()
        from tinyagentos.app import create_app
        app = create_app(data_dir=tmp_path)
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        admin_record = app.state.auth.find_user("admin")
        invite_code = app.state.auth.add_user_invite("bob", "admin")
        app.state.auth.complete_invite("bob", invite_code, "Bob User", "bob@test.com", "bobpass123")
        bob_record = app.state.auth.find_user("bob")
        bob_token = app.state.auth.create_session(user_id=bob_record["id"], long_lived=True)
        app.state._startup_complete = True
        mock_registry = MagicMock()
        mock_registry.get_by_handle = AsyncMock(return_value=None)
        app.state.agent_registry = mock_registry
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as c:
            resp = await c.get("/api/agents/test-agent/versions/abc12345/diff")
        assert resp.status_code == 403

    async def test_revert_403_when_ownership_unresolved(self, tmp_path):
        config = {
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [
                {"name": "test-agent", "host": "192.168.1.100", "qmd_index": "test", "color": "#98fb98"}
            ],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        (tmp_path / ".setup_complete").touch()
        from tinyagentos.app import create_app
        app = create_app(data_dir=tmp_path)
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        admin_record = app.state.auth.find_user("admin")
        invite_code = app.state.auth.add_user_invite("bob", "admin")
        app.state.auth.complete_invite("bob", invite_code, "Bob User", "bob@test.com", "bobpass123")
        bob_record = app.state.auth.find_user("bob")
        bob_token = app.state.auth.create_session(user_id=bob_record["id"], long_lived=True)
        app.state._startup_complete = True
        mock_registry = MagicMock()
        mock_registry.get_by_handle = AsyncMock(return_value=None)
        app.state.agent_registry = mock_registry
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as c:
            resp = await c.post("/api/agents/test-agent/versions/abc12345/revert")
        assert resp.status_code == 403

    async def test_diff_container_unreachable_returns_409(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(1, "incus: instance not found")),
        ):
            resp = await client.get("/api/agents/test-agent/versions/abc12345/diff")
        assert resp.status_code == 409
        assert resp.json()["error"] == "container_unreachable"

    async def test_revert_container_unreachable_returns_409(self, client):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(1, "incus: instance not found")),
        ):
            resp = await client.post("/api/agents/test-agent/versions/abc12345/revert")
        assert resp.status_code == 409
        assert resp.json()["error"] == "container_unreachable"
