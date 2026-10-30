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
    @pytest.mark.parametrize("name", ["..", "%2e%2e", "..%2F.."])
    async def test_dot_names_do_not_list_the_data_dir(self, client, name):
        data_dir = client._transport.app.state.config_path.parent
        (data_dir / "workspaces").mkdir(exist_ok=True)
        resp = await client.get(f"/api/agents/{name}/files")
        assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_real_agent_workspace_still_lists(self, client):
        data_dir = client._transport.app.state.config_path.parent
        ws = data_dir / "workspaces" / "test-agent"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "notes.txt").write_text("hi")
        resp = await client.get("/api/agents/test-agent/files")
        assert resp.status_code == 200
        assert [f["name"] for f in resp.json()] == ["notes.txt"]
