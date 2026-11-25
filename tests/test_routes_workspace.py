import pytest


class TestWorkspaceRoutes:
    @pytest.mark.asyncio
    async def test_send_inter_agent_message(self, client):
        resp = await client.post("/api/agents/test-agent/messages", json={
            "from_agent": "other-agent",
            "message": "Hello from test",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_contacts(self, client):
        resp = await client.get("/api/agents/test-agent/workspace/messages/contacts")
        assert resp.status_code == 200


class TestLegacyAgentFilesContainment:
    """GET /api/agents/{name}/files must not list anything outside workspaces/."""

    @pytest.mark.asyncio
    async def test_dot_name_does_not_list_the_data_dir(self, client):
        # httpx normalises a bare ".." and the router refuses "..%2F..", so
        # the encoded form is the one that reaches the containment check.
        data_dir = client._transport.app.state.config_path.parent
        (data_dir / "workspaces").mkdir(exist_ok=True)
        resp = await client.get("/api/agents/%2e%2e/files")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_escaping_symlink_is_refused(self, client, tmp_path):
        data_dir = client._transport.app.state.config_path.parent
        (data_dir / "workspaces").mkdir(exist_ok=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        try:
            (data_dir / "workspaces" / "escape").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported in this environment")
        resp = await client.get("/api/agents/escape/files")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_real_agent_workspace_still_lists(self, client):
        data_dir = client._transport.app.state.config_path.parent
        ws = data_dir / "workspaces" / "test-agent"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "notes.txt").write_text("hi")
        resp = await client.get("/api/agents/test-agent/files")
        assert resp.status_code == 200
        assert [f["name"] for f in resp.json()] == ["notes.txt"]
