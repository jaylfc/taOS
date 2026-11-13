import pytest
import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock


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


@pytest.mark.asyncio
class TestArchiveSnapshotTimeout:
    """archive_agent_fully must return 504 when snapshot_create times out."""

    async def test_archive_returns_504_on_snapshot_timeout(self, client, monkeypatch):
        app = client._transport.app
        config = app.state.config

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            raise asyncio.TimeoutError()

        async def fake_container_exists(name):
            return True

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr(
            "tinyagentos.containers.snapshot_create", fake_snapshot_create
        )
        monkeypatch.setattr(
            "tinyagentos.containers.container_exists", fake_container_exists
        )

        # Need an agent to archive
        config.agents.append(
            {"name": "test-agent", "host": "", "qmd_index": "", "color": ""}
        )
        from tinyagentos.config import save_config_locked
        await save_config_locked(config, config.config_path)

        resp = await client.delete("/api/agents/test-agent")

        # Should return 504 with descriptive error
        assert resp.status_code == 504
        data = resp.json()
        assert "error" in data
        assert "archive failed: snapshot" in data["error"]
        assert "taos-agent-test-agent" in data["error"]
        assert "taos-archive-" in data["error"]
        assert "1800s" in data["error"]
        assert "dir-backed pools copy the full rootfs" in data["error"]
        assert "Agent left in live list" in data["error"]
        assert "incus snapshot list" in data["error"]

        # Agent must still be in config (config not mutated)
        agent_names = [a["name"] for a in config.agents]
        assert "test-agent" in agent_names


@pytest.mark.asyncio
class TestPurgePathTraversal:
    """purge_archived must not escape the archive/ directory via missing or
    malicious archive_dir values."""

    def _setup_purge_test(self, app, config, archive_dir, sentinel_path):
        """Create an archived entry with the given archive_dir and a sentinel
        file at sentinel_path. Returns the archive_id."""
        entry = {
            "id": "purge-test-123",
            "archived_at": "20260101T000000",
            "archived_slug": "test-agent",
            "snapshot_name": "snap-001",
            "export_path": None,
            "archive_dir": archive_dir,
            "original": {"name": "test-agent", "chat_channel_id": None},
            "container_name": "taos-agent-test-agent",
        }
        config.archived_agents = [entry]
        # Ensure sentinel exists
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.write_text("sentinel")
        return entry["id"]

    async def _fake_destroy(self, name):
        return {"success": True, "output": ""}

    async def test_purge_missing_archive_dir_spares_data_dir(self, client, monkeypatch, app, tmp_path):
        """archive_dir missing (empty string): data_dir sentinel must survive."""
        config = app.state.config
        data_dir = app.state.data_dir
        sentinel = data_dir / "sentinel.txt"
        archive_id = self._setup_purge_test(app, config, "", sentinel)

        monkeypatch.setattr("tinyagentos.containers.destroy_container", self._fake_destroy)

        resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "purged"
        # Sentinel in data_dir must still exist
        assert sentinel.exists(), "data_dir sentinel was deleted!"

    async def test_purge_absolute_archive_dir_spares_outside(self, client, monkeypatch, app, tmp_path):
        """archive_dir is absolute path outside data_dir: that outside sentinel must survive."""
        config = app.state.config
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        sentinel = outside_dir / "sentinel.txt"
        archive_id = self._setup_purge_test(app, config, str(outside_dir), sentinel)

        monkeypatch.setattr("tinyagentos.containers.destroy_container", self._fake_destroy)

        resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "purged"
        assert sentinel.exists(), "Outside sentinel was deleted!"

    async def test_purge_archive_dir_with_dotdot_spares_data_dir(self, client, monkeypatch, app):
        """archive_dir contains '..' to escape archive/: data_dir sentinel must survive."""
        config = app.state.config
        data_dir = app.state.data_dir
        sentinel = data_dir / "sentinel.txt"
        # archive/../ escapes to data_dir
        archive_id = self._setup_purge_test(app, config, "archive/../", sentinel)

        monkeypatch.setattr("tinyagentos.containers.destroy_container", self._fake_destroy)

        resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "purged"
        assert sentinel.exists(), "data_dir sentinel was deleted via dotdot!"

    async def test_purge_normal_archive_dir_removes_subdir(self, client, monkeypatch, app):
        """Positive control: archive/<subdir> IS removed."""
        config = app.state.config
        data_dir = app.state.data_dir
        archive_subdir = data_dir / "archive" / "test-agent-20260101T000000"
        archive_subdir.mkdir(parents=True)
        sentinel = archive_subdir / "sentinel.txt"
        sentinel.write_text("sentinel")
        archive_id = self._setup_purge_test(
            app, config, "archive/test-agent-20260101T000000", sentinel
        )

        monkeypatch.setattr("tinyagentos.containers.destroy_container", self._fake_destroy)

        resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "purged"
        # The archive subdir should be gone
        assert not archive_subdir.exists(), "Normal archive dir was not removed!"


@pytest.mark.asyncio
class TestArchiveChannelFlagAfterRevoke:
    async def test_channel_not_archived_when_revoke_fails(self, client, monkeypatch):
        app = client._transport.app
        config = app.state.config
        auth = app.state.auth

        # Create a chat channel and link it to the agent.
        channel = await app.state.chat_channels.create_channel(
            name="dm-test-agent",
            type="dm",
            created_by="test-user",
            members=[],
        )
        channel_id = channel["id"]

        agent = next(a for a in config.agents if a["name"] == "test-agent")
        agent["chat_channel_id"] = channel_id
        from tinyagentos.config import save_config_locked
        await save_config_locked(config, config.config_path)

        token = auth.mint_agent_local_token("test-agent")
        assert auth.validate_local_token(token) is True

        async def fake_container_exists(name):
            return True

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        def failing_revoke(request, slug, agent_id):
            raise RuntimeError("revoke boom")

        monkeypatch.setattr(
            "tinyagentos.containers.container_exists", fake_container_exists
        )
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr(
            "tinyagentos.containers.snapshot_create", fake_snapshot_create
        )
        monkeypatch.setattr(
            "tinyagentos.routes.agent_archive._revoke_gateway_keys",
            failing_revoke,
        )

        resp = await client.delete("/api/agents/test-agent")

        assert resp.status_code == 500
        assert "credential revoke failed" in resp.json().get("error", "")

        # Agent must still be in config (archive not completed).
        assert any(a["name"] == "test-agent" for a in config.agents)
        # Archive entry must not have been created.
        assert not any(
            e.get("archived_slug") == "test-agent"
            for e in getattr(config, "archived_agents", [])
        )

        # Channel must not be flagged archived.
        ch = await app.state.chat_channels.get_channel(channel_id)
        settings = (ch or {}).get("settings") or {}
        assert settings.get("archived") is not True
