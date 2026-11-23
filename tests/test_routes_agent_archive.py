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

    async def test_purge_with_live_agent_keeps_deploy_task(
        self, client, monkeypatch
    ):
        """Purge with a live agent holding the same name keeps the live
        agent's deploy_tasks entry."""
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

        # A fresh deploy takes the freed slug and creates a deploy_tasks entry.
        config.agents.append(
            {"name": "test-agent", "host": "", "qmd_index": "", "color": ""}
        )
        from tinyagentos.config import save_config_locked
        await save_config_locked(config, config.config_path)
        app.state.deploy_tasks["test-agent"] = {
            "status": "deployed",
            "name": "test-agent",
        }

        purge_resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert purge_resp.status_code == 200
        assert purge_resp.json()["status"] == "purged"

        # Live agent's deploy_tasks entry must survive.
        assert "test-agent" in app.state.deploy_tasks
        assert app.state.deploy_tasks["test-agent"]["status"] == "deployed"

    async def test_purge_without_live_agent_removes_deploy_task(
        self, client, monkeypatch
    ):
        """Purge without a live agent holding the name removes the
        deploy_tasks entry."""
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

        # Seed a stale deploy task (no live agent with this name).
        app.state.deploy_tasks["test-agent"] = {
            "status": "deployed",
            "name": "test-agent",
        }

        purge_resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert purge_resp.status_code == 200
        assert purge_resp.json()["status"] == "purged"

        # Stale deploy_tasks entry must be removed.
        assert "test-agent" not in app.state.deploy_tasks


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

        def failing_revoke(request, slug, agent_id):
            raise RuntimeError("revoke boom")

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


@pytest.mark.asyncio
class TestArchiveDropsDeployTask:
    """Archiving an agent must clear any stale deploy_tasks entry for its slug."""

    async def test_archive_drops_deploy_task(self, client, monkeypatch, app):
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

        # Seed a stale deploy task under the agent's name.
        app.state.deploy_tasks["test-agent"] = {
            "status": "deployed",
            "name": "test-agent",
        }

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
        assert "test-agent" not in app.state.deploy_tasks


@pytest.mark.asyncio
class TestRestoreDropsDeployTask:
    """Restoring an agent must clear any stale deploy_tasks entry for the
    restored name."""

    async def test_restore_drops_deploy_task(self, client, monkeypatch, app):
        async def fake_deploy(req):
            return {
                "success": True,
                "name": req.name,
                "ip": "10.0.0.50",
                "llm_key": "sk-x",
                "steps": [],
                "container": f"taos-agent-{req.name}",
            }

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_snapshot_restore(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_start(name):
            return {"success": True, "output": ""}

        async def fake_set_env(name, key, value):
            return {"success": True, "output": ""}

        async def fake_exec(name, cmd, timeout=300):
            return (0, "")

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr(
            "tinyagentos.containers.snapshot_create", fake_snapshot_create
        )
        monkeypatch.setattr(
            "tinyagentos.containers.snapshot_restore", fake_snapshot_restore
        )
        monkeypatch.setattr("tinyagentos.containers.start_container", fake_start)
        monkeypatch.setattr("tinyagentos.containers.set_env", fake_set_env)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        async def fake_container_exists(name):
            return True
        monkeypatch.setattr(
            "tinyagentos.containers.container_exists", fake_container_exists
        )

        await client.post("/api/agents/deploy", json={"name": "test-agent", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)
        await client.delete("/api/agents/test-agent")

        archived = (await client.get("/api/agents/archived")).json()
        archive_id = archived[0]["id"]

        # Seed a stale deploy task under the restored name before restore.
        app.state.deploy_tasks["test-agent"] = {
            "status": "deployed",
            "name": "test-agent",
        }

        resp = await client.post(f"/api/agents/archived/{archive_id}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"
        assert "test-agent" not in app.state.deploy_tasks


class TestRestorePublishesConfigBeforeContainerStart:
    """The restored agent's local token is bound to the registry canonical id,
    so the middleware looks up the CURRENT config entry for a canonical-bound
    token. Before the fix, _restore_archived minted the token (step 1b), started
    the container (step 3) and only appended the config entry at step 8, so the
    bridge's bootstrap request during steps 3-7 answered 401.

    The fix moves step 4 (key mint) and step 8 (config append) before step 3
    (start_container), so the entry exists when the container starts.
    """

    @pytest.mark.asyncio
    async def test_restore_publishes_config_before_container_start(self, client, monkeypatch, app):
        from tinyagentos.agent_db import find_agent

        config = app.state.config
        entry = {
            "id": "ghost-1",
            "archived_at": "20260101T000000",
            "archived_slug": "ghost",
            "snapshot_name": "taos-archive-1",
            "export_path": None,
            "archive_dir": "archive/ghost-1",
            "container_name": "taos-agent-ghost",
            "original": {"name": "ghost", "registry_canonical_id": "cid-ghost"},
        }
        config.archived_agents = [entry]

        async def ok(*args, **kwargs):
            return {"success": True, "output": ""}

        seen_at_start = []

        async def fake_start(name):
            # At the moment start_container is called the restored entry must
            # already be in config.agents with the archived entry's
            # registry_canonical_id, so the middleware can fence the bridge's
            # bootstrap request (GET /api/openclaw/bootstrap with the token).
            # Record rather than assert: _restore_archived swallows exceptions
            # from start_container, so an assertion here would be invisible.
            seen_at_start.append(find_agent(config, "ghost"))
            return {"success": True}

        monkeypatch.setattr("tinyagentos.containers.snapshot_restore", ok)
        monkeypatch.setattr("tinyagentos.containers.rename_container", ok)
        monkeypatch.setattr("tinyagentos.containers.start_container", fake_start)
        monkeypatch.setattr("tinyagentos.containers.set_env", ok)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", ok)

        resp = await client.post("/api/agents/archived/ghost-1/restore")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "restored"
        assert seen_at_start, "start_container was never called"
        agent_at_start = seen_at_start[0]
        assert agent_at_start is not None, (
            "restored config entry missing at container start: the bridge's "
            "bootstrap request would answer 401"
        )
        assert (
            agent_at_start.get("registry_canonical_id") == "cid-ghost"
        ), "restored entry lost its canonical id"
        assert any(a["name"] == "ghost" for a in config.agents)
        assert not any(e["id"] == "ghost-1" for e in config.archived_agents)


@pytest.mark.asyncio
class TestRestoreRollbackOnSaveFailure:
    """If save_config_locked raises during restore, the in-memory config
    mutation (moving the entry from archived_agents to agents) must be
    rolled back so the archive entry is still present and the agent is not
    duplicated in the live list.
    """

    async def test_restore_rolls_back_in_memory_on_save_failure(self, client, monkeypatch, app):
        config = app.state.config
        entry = {
            "id": "ghost-1",
            "archived_at": "20260101T000000",
            "archived_slug": "ghost",
            "snapshot_name": "taos-archive-1",
            "export_path": None,
            "archive_dir": "archive/ghost-1",
            "container_name": "taos-agent-ghost",
            "original": {"name": "ghost", "registry_canonical_id": "cid-ghost"},
        }
        config.archived_agents = [entry]
        original_archived = list(config.archived_agents)
        original_agents = list(config.agents)

        async def ok(*args, **kwargs):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.snapshot_restore", ok)
        monkeypatch.setattr("tinyagentos.containers.rename_container", ok)
        monkeypatch.setattr("tinyagentos.containers.start_container", ok)
        monkeypatch.setattr("tinyagentos.containers.set_env", ok)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", ok)

        # Patch save_config_locked to raise
        async def failing_save(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr("tinyagentos.routes.agent_archive.save_config_locked", failing_save)

        resp = await client.post("/api/agents/archived/ghost-1/restore")
        assert resp.status_code == 500
        assert "disk full" in resp.json().get("error", "")

        # In-memory state must be rolled back
        assert config.archived_agents == original_archived
        assert config.agents == original_agents

    @pytest.mark.asyncio
    async def test_slug_released_after_save_failure_allows_second_restore(
        self, client, monkeypatch, app
    ):
        """After a failing config save, the slug reservation is released so a
        second restore (or import/deploy) under the same name succeeds, and no
        half-restored agent record or container remains.
        """
        config = app.state.config
        entry = {
            "id": "ghost-1",
            "archived_at": "20260101T000000",
            "archived_slug": "ghost",
            "snapshot_name": "taos-archive-1",
            "export_path": None,
            "archive_dir": "archive/ghost-1",
            "container_name": "taos-agent-ghost",
            "original": {"name": "ghost", "registry_canonical_id": "cid-ghost"},
        }
        config.archived_agents = [entry]

        async def ok(*args, **kwargs):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.snapshot_restore", ok)
        monkeypatch.setattr("tinyagentos.containers.rename_container", ok)
        monkeypatch.setattr("tinyagentos.containers.start_container", ok)
        monkeypatch.setattr("tinyagentos.containers.set_env", ok)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", ok)

        # First restore fails on save
        async def failing_save(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr("tinyagentos.routes.agent_archive.save_config_locked", failing_save)

        resp = await client.post("/api/agents/archived/ghost-1/restore")
        assert resp.status_code == 500
        assert "disk full" in resp.json().get("error", "")

        # Archive entry must still exist (rollback worked)
        assert any(e["id"] == "ghost-1" for e in config.archived_agents)
        assert not any(a["name"] == "ghost" for a in config.agents)

        # Second restore should succeed (slug was released)
        async def success_save(*args, **kwargs):
            return None

        monkeypatch.setattr("tinyagentos.routes.agent_archive.save_config_locked", success_save)

        resp2 = await client.post("/api/agents/archived/ghost-1/restore")
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["status"] == "restored"
        assert resp2.json()["name"] == "ghost"

        # Agent is now live, archive entry is gone
        assert any(a["name"] == "ghost" for a in config.agents)
        assert not any(e["id"] == "ghost-1" for e in config.archived_agents)

        # No half-restored record: the restored agent has the correct canonical_id
        restored_agent = next(a for a in config.agents if a["name"] == "ghost")
        assert restored_agent.get("registry_canonical_id") == "cid-ghost"
        assert restored_agent.get("status") == "stopped"