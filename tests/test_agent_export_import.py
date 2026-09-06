"""Tests for agent export/import endpoints."""
import pytest

from tinyagentos.agent_db import find_agent
from tinyagentos.config import load_config


@pytest.mark.asyncio
class TestAgentExport:
    async def test_export_existing_agent(self, client):
        resp = await client.get("/api/agents/test-agent/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 1
        assert data["agent"]["name"] == "test-agent"
        assert "channels" in data
        assert "groups" in data

    async def test_export_nonexistent_agent(self, client):
        resp = await client.get("/api/agents/nobody/export")
        assert resp.status_code == 404

    async def test_export_includes_channels(self, client):
        # Add a channel first
        channel_store = client._transport.app.state.channels
        await channel_store.add("test-agent", "telegram", {"bot_token_secret": "tg-test"})

        resp = await client.get("/api/agents/test-agent/export")
        data = resp.json()
        assert len(data["channels"]) >= 1
        assert data["channels"][0]["type"] == "telegram"
        assert data["channels"][0]["config"]["bot_token_secret"] == "tg-test"

    async def test_export_includes_groups(self, client):
        # Create a group and add test-agent
        relationship_mgr = client._transport.app.state.relationships
        gid = await relationship_mgr.create_group("design-team", description="Design")
        await relationship_mgr.add_member(gid, "test-agent")

        resp = await client.get("/api/agents/test-agent/export")
        data = resp.json()
        assert "design-team" in data["groups"]


@pytest.mark.asyncio
class TestAgentImport:
    async def test_import_new_agent(self, client):
        payload = {
            "version": 1,
            "agent": {"name": "imported-agent", "host": "10.0.0.50", "color": "#ff0000"},
            "channels": [{"type": "web-chat", "config": {}}],
            "groups": [],
        }
        resp = await client.post("/api/agents/import", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "imported"
        assert data["name"] == "imported-agent"

        # Verify agent exists
        resp2 = await client.get("/api/agents/imported-agent")
        assert resp2.status_code == 200
        assert resp2.json()["name"] == "imported-agent"

    async def test_import_duplicate_agent_fails(self, client):
        payload = {
            "version": 1,
            "agent": {"name": "test-agent", "host": "10.0.0.1"},
            "channels": [],
            "groups": [],
        }
        resp = await client.post("/api/agents/import", json=payload)
        assert resp.status_code == 409

    async def test_import_restores_channels(self, client):
        payload = {
            "version": 1,
            "agent": {"name": "ch-agent", "host": "10.0.0.60"},
            "channels": [
                {"type": "telegram", "config": {"bot_token_secret": "tg-imported"}},
                {"type": "web-chat", "config": {}},
            ],
            "groups": [],
        }
        resp = await client.post("/api/agents/import", json=payload)
        assert resp.status_code == 200

        channel_store = client._transport.app.state.channels
        channels = await channel_store.list_for_agent("ch-agent")
        types = {ch["type"] for ch in channels}
        assert "telegram" in types
        assert "web-chat" in types

    async def test_import_restores_group_membership(self, client):
        # Create group first
        relationship_mgr = client._transport.app.state.relationships
        await relationship_mgr.create_group("eng-team", description="Engineering")

        payload = {
            "version": 1,
            "agent": {"name": "grp-agent", "host": "10.0.0.70"},
            "channels": [],
            "groups": ["eng-team"],
        }
        resp = await client.post("/api/agents/import", json=payload)
        assert resp.status_code == 200

        groups = await relationship_mgr.get_agent_groups("grp-agent")
        group_names = [g["name"] for g in groups]
        assert "eng-team" in group_names

    async def test_import_missing_name_fails(self, client):
        payload = {
            "version": 1,
            "agent": {"host": "10.0.0.1"},
            "channels": [],
            "groups": [],
        }
        resp = await client.post("/api/agents/import", json=payload)
        assert resp.status_code == 400

    async def test_roundtrip_export_import(self, client):
        """Export an agent and import it under a new name."""
        # Add a channel to test-agent
        channel_store = client._transport.app.state.channels
        await channel_store.add("test-agent", "web-chat", {})

        # Export
        resp = await client.get("/api/agents/test-agent/export")
        exported = resp.json()

        # Modify name for import
        exported["agent"]["name"] = "cloned-agent"

        # Import
        resp2 = await client.post("/api/agents/import", json=exported)
        assert resp2.status_code == 200

        # Verify cloned agent has channels
        channels = await channel_store.list_for_agent("cloned-agent")
        assert len(channels) >= 1

    async def test_import_strips_privileged_keys_and_slugifies_name(
        self, client, tmp_data_dir
    ):
        """S2-18: an import bundle must not persist privileged keys and the
        agent name must be slugified to a container-safe identifier."""
        payload = {
            "version": 1,
            "agent": {
                "name": "My Agent",
                "host": "10.0.0.50",
                "color": "#ff0000",
                "llm_key": "super-secret-key",
                "can_read_user_memory": True,
                "registry_canonical_id": "evil-canonical-id",
                "permitted_models": ["gpt-4"],
            },
            "channels": [],
            "groups": [],
        }
        resp = await client.post("/api/agents/import", json=payload)
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-agent"

        config = load_config(tmp_data_dir / "config.yaml")
        imported = find_agent(config, "my-agent")
        assert imported is not None, "agent name was not slugified"
        assert imported["name"] == "my-agent"
        assert imported["display_name"] == "My Agent"

        for key in (
            "llm_key",
            "can_read_user_memory",
            "registry_canonical_id",
            "permitted_models",
        ):
            assert key not in imported, f"privileged key persisted: {key}"

        assert imported["host"] == "10.0.0.50"
        assert imported["color"] == "#ff0000"
