import pytest


class TestListArchivedAgents:
    @pytest.mark.asyncio
    async def test_empty_archive_returns_empty_list(self, client, monkeypatch, app):
        monkeypatch.setattr(app.state.config, "archived_agents", [])
        resp = await client.get("/api/agents/archived")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data == []

    @pytest.mark.asyncio
    async def test_returns_archived_entries(self, client, monkeypatch, app):
        entries = [
            {
                "id": "abc123",
                "archived_at": "20260101T000000",
                "archived_slug": "my-agent",
                "snapshot_name": "snap-001",
                "export_path": None,
                "archive_dir": "archive/my-agent-20260101T000000",
                "original": {"name": "my-agent"},
            },
            {
                "id": "def456",
                "archived_at": "20260202T000000",
                "archived_slug": "other-agent",
                "snapshot_name": None,
                "export_path": None,
                "archive_dir": "archive/other-agent-20260202T000000",
                "original": {"name": "other-agent"},
            },
        ]
        monkeypatch.setattr(app.state.config, "archived_agents", entries)
        resp = await client.get("/api/agents/archived")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["id"] == "abc123"
        assert data[0]["archived_slug"] == "my-agent"
        assert data[0]["snapshot_name"] == "snap-001"
        assert data[1]["id"] == "def456"
        assert data[1]["archived_slug"] == "other-agent"
        assert data[1]["snapshot_name"] is None

    @pytest.mark.asyncio
    async def test_tombstoned_entry_has_null_snapshot(self, client, monkeypatch, app):
        entries = [
            {
                "id": "tomb1",
                "archived_at": "20260303T000000",
                "archived_slug": "failed-deploy",
                "snapshot_name": None,
                "export_path": None,
                "archive_dir": "archive/failed-deploy-20260303T000000",
                "original": {"name": "failed-deploy"},
            }
        ]
        monkeypatch.setattr(app.state.config, "archived_agents", entries)
        resp = await client.get("/api/agents/archived")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["snapshot_name"] is None
        assert data[0]["archived_slug"] == "failed-deploy"


@pytest.mark.asyncio
class TestArchiveRevokesLocalToken:
    """Archive frees the agent's slug, so the archived agent's local
    token must stop validating as an admin credential."""

    async def test_archive_revokes_archived_agent_token(
        self, client, monkeypatch
    ):
        app = client._transport.app
        auth = app.state.auth
        token = auth.mint_agent_local_token("test-agent")
        assert auth.validate_local_token(token) is True

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_container_exists(name):
            return True

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr(
            "tinyagentos.containers.snapshot_create", fake_snapshot_create
        )
        monkeypatch.setattr(
            "tinyagentos.containers.container_exists", fake_container_exists
        )

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

        assert auth.validate_local_token(token) is False
        assert auth.get_local_token_agent(token) is None


@pytest.mark.asyncio
class TestPurgeSlugReuse:
    """A live agent that redeployed onto the freed slug keeps its
    local token when the old archive is purged."""

    async def test_purge_with_live_agent_same_slug_keeps_token(
        self, client, monkeypatch
    ):
        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_destroy(name):
            return {"success": True, "output": ""}

        async def fake_container_exists(name):
            return True

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr(
            "tinyagentos.containers.snapshot_create", fake_snapshot_create
        )
        monkeypatch.setattr(
            "tinyagentos.containers.destroy_container", fake_destroy
        )
        monkeypatch.setattr(
            "tinyagentos.containers.container_exists", fake_container_exists
        )

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

        app = client._transport.app
        config = app.state.config
        entry = config.archived_agents[-1]
        archive_id = entry["id"]

        # A fresh deploy takes the freed slug and mints its own token.
        config.agents.append(
            {"name": "test-agent", "host": "", "qmd_index": "", "color": ""}
        )
        from tinyagentos.config import save_config_locked
        await save_config_locked(config, config.config_path)
        new_token = app.state.auth.mint_agent_local_token("test-agent")
        assert app.state.auth.validate_local_token(new_token) is True

        purge_resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert purge_resp.status_code == 200
        assert purge_resp.json()["status"] == "purged"

        assert app.state.auth.validate_local_token(new_token) is True
        assert app.state.auth.get_local_token_agent(new_token) == "test-agent"
