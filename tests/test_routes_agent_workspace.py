"""Tests for the per-agent workspace file browser routes."""
import io
import pytest


def _add_agent(app, name: str) -> None:
    """Register an agent so _agent_exists() returns True."""
    app.state.config.agents.append({
        "name": name,
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#cccccc",
    })


class TestAgentWorkspaceRoutes:

    @pytest.mark.asyncio
    async def test_list_returns_404_for_unknown_agent(self, client, app):
        """Listing workspace for an agent not in the registry returns 404."""
        resp = await client.get("/api/agents/ghost-agent/workspace/files")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_files_returns_entries(self, client, app):
        """Files dropped into the agent workspace appear in the listing."""
        _add_agent(app, "alpha")
        ws = app.state.agent_workspaces_dir / "alpha"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "one.txt").write_bytes(b"one")
        (ws / "two.txt").write_bytes(b"twotwo")

        resp = await client.get("/api/agents/alpha/workspace/files")
        assert resp.status_code == 200
        entries = {e["name"]: e for e in resp.json()}
        assert set(entries) == {"one.txt", "two.txt"}
        assert entries["one.txt"]["size"] == 3
        assert entries["two.txt"]["size"] == 6

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, client, app):
        """Path traversal in the ?path= query is rejected with 400."""
        _add_agent(app, "beta")
        resp = await client.get("/api/agents/beta/workspace/files?path=../../etc")
        assert resp.status_code == 400

        resp2 = await client.get("/api/agents/beta/workspace/files?path=foo/../../bar")
        assert resp2.status_code == 400

    @pytest.mark.asyncio
    async def test_symlink_outside_blocked(self, client, app, tmp_path):
        """A symlink pointing outside the workspace resolves outside root
        and is rejected when listed."""
        _add_agent(app, "gamma")
        ws = app.state.agent_workspaces_dir / "gamma"
        ws.mkdir(parents=True, exist_ok=True)

        outside = tmp_path / "escape-target"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")

        link = ws / "escape"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported in this environment")

        resp = await client.get("/api/agents/gamma/workspace/files?path=escape")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_writes_to_agent_workspace(self, client, app):
        """POST upload lands the bytes in data/agent-workspaces/<name>/."""
        _add_agent(app, "delta")
        content = b"uploaded"
        resp = await client.post(
            "/api/agents/delta/workspace/files/upload",
            files={"file": ("hello.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "uploaded"

        dest = app.state.agent_workspaces_dir / "delta" / "hello.txt"
        assert dest.exists()
        assert dest.read_bytes() == content

    @pytest.mark.asyncio
    async def test_mkdir_within_agent_workspace_only(self, client, app):
        """mkdir must stay inside the agent workspace — '..' escapes are 400."""
        _add_agent(app, "epsilon")

        # Valid mkdir
        ok = await client.post(
            "/api/agents/epsilon/workspace/mkdir",
            json={"path": "sub"},
        )
        assert ok.status_code == 200
        assert (app.state.agent_workspaces_dir / "epsilon" / "sub").is_dir()

        # Escape attempt
        bad = await client.post(
            "/api/agents/epsilon/workspace/mkdir",
            json={"path": "../escaped"},
        )
        assert bad.status_code == 400
        assert not (app.state.agent_workspaces_dir / "escaped").exists()

        # Absolute path attempt
        abs_bad = await client.post(
            "/api/agents/epsilon/workspace/mkdir",
            json={"path": "/tmp/escape"},
        )
        assert abs_bad.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_blocks_outside_workspace(self, client, app):
        """DELETE with '..' escape is rejected without touching the target."""
        _add_agent(app, "zeta")
        ws = app.state.agent_workspaces_dir / "zeta"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "keep.txt").write_bytes(b"keep")

        resp = await client.delete("/api/agents/zeta/workspace/files/..%2Fkeep.txt")
        assert resp.status_code == 400
        # Original file untouched
        assert (ws / "keep.txt").exists()

    @pytest.mark.asyncio
    async def test_download_file(self, client, app):
        """GET on a file path streams the file contents."""
        _add_agent(app, "eta")
        ws = app.state.agent_workspaces_dir / "eta"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "readme.txt").write_bytes(b"hello from agent")

        resp = await client.get("/api/agents/eta/workspace/files/readme.txt")
        assert resp.status_code == 200
        assert resp.content == b"hello from agent"

    @pytest.mark.asyncio
    async def test_stats_returns_totals(self, client, app):
        """GET /stats returns total_files and total_size."""
        _add_agent(app, "theta")
        ws = app.state.agent_workspaces_dir / "theta"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "a.txt").write_bytes(b"12345")
        (ws / "b.txt").write_bytes(b"67890")

        resp = await client.get("/api/agents/theta/workspace/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 2
        assert data["total_size"] == 10
