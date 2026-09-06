"""Endpoint tests for tinyagentos/routes/user_memory.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def user_memory(client, tmp_path):
    """Ensure user_memory store is initialized on the test app."""
    store = client._transport.app.state.user_memory
    if store._db is not None:
        await store.close()
    # Point the store at a tmp db so tests are isolated
    store.db_path = tmp_path / "user_memory.db"
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestGetStats:
    async def test_empty_stats(self, client, user_memory):
        resp = await client.get("/api/user-memory/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["collections"] == {}

    async def test_stats_after_save(self, client, user_memory):
        await user_memory.save_chunk("user", "hello world", "test", "snippets")
        resp = await client.get("/api/user-memory/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert "snippets" in data["collections"]


@pytest.mark.asyncio
class TestGetSettings:
    async def test_default_settings(self, client, user_memory):
        resp = await client.get("/api/user-memory/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "capture_conversations" in data
        assert "capture_files" in data
        assert "capture_searches" in data
        assert "capture_notes" in data
        assert data["capture_conversations"] is True

    async def test_settings_shape(self, client, user_memory):
        resp = await client.get("/api/user-memory/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert len(data) >= 4


@pytest.mark.asyncio
class TestUpdateSettings:
    async def test_update_single_setting(self, client, user_memory):
        resp = await client.put(
            "/api/user-memory/settings",
            json={"capture_conversations": False},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_update_persists(self, client, user_memory):
        await client.put(
            "/api/user-memory/settings",
            json={"capture_searches": True},
        )
        settings = await user_memory.get_settings("user")
        assert settings["capture_searches"] is True

    async def test_update_defaults_preserved(self, client, user_memory):
        await client.put(
            "/api/user-memory/settings",
            json={"capture_notes": False},
        )
        settings = await user_memory.get_settings("user")
        # Defaults not in the update payload should remain
        assert settings["capture_conversations"] is True


@pytest.mark.asyncio
class TestBrowse:
    async def test_empty_browse(self, client, user_memory):
        resp = await client.get("/api/user-memory/browse")
        assert resp.status_code == 200
        data = resp.json()
        assert "chunks" in data
        assert data["chunks"] == []

    async def test_browse_returns_saved_chunks(self, client, user_memory):
        await user_memory.save_chunk("user", "chunk one", "title1", "snippets")
        await user_memory.save_chunk("user", "chunk two", "title2", "notes")
        resp = await client.get("/api/user-memory/browse")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chunks"]) == 2

    async def test_browse_filter_by_collection(self, client, user_memory):
        await user_memory.save_chunk("user", "snippet content", "s", "snippets")
        await user_memory.save_chunk("user", "note content", "n", "notes")
        resp = await client.get("/api/user-memory/browse?collection=snippets")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chunks"]) == 1
        assert data["chunks"][0]["collection"] == "snippets"


@pytest.mark.asyncio
class TestSave:
    async def test_save_returns_hash(self, client, user_memory):
        resp = await client.post(
            "/api/user-memory/save",
            json={"content": "hello memory", "title": "greeting"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert isinstance(data["hash"], str)
        assert len(data["hash"]) == 16

    async def test_save_missing_content(self, client, user_memory):
        resp = await client.post(
            "/api/user-memory/save",
            json={"title": "no content"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "content required"

    async def test_save_default_collection(self, client, user_memory):
        resp = await client.post(
            "/api/user-memory/save",
            json={"content": "test content"},
        )
        assert resp.status_code == 200
        chunks = await user_memory.browse("user")
        assert len(chunks) == 1
        assert chunks[0]["collection"] == "snippets"

    async def test_save_custom_collection(self, client, user_memory):
        resp = await client.post(
            "/api/user-memory/save",
            json={"content": "test", "collection": "journal"},
        )
        assert resp.status_code == 200
        chunks = await user_memory.browse("user", collection="journal")
        assert len(chunks) == 1

    async def test_save_taosmd_ingest_failure_is_nonfatal(self, client, user_memory):
        """Save still returns 200 even if taosmd ingest fails."""
        with patch.object(
            client._transport.app.state.http_client,
            "post",
            side_effect=ConnectionError("taosmd down"),
        ):
            resp = await client.post(
                "/api/user-memory/save",
                json={"content": "test fail ingest"},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


@pytest.mark.asyncio
class TestDeleteChunk:
    async def test_delete_existing(self, client, user_memory):
        h = await user_memory.save_chunk("user", "to delete", "del", "snippets")
        resp = await client.delete(f"/api/user-memory/chunk/{h}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_delete_nonexistent(self, client, user_memory):
        resp = await client.delete("/api/user-memory/chunk/nonexist")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    async def test_delete_actually_removes(self, client, user_memory):
        h = await user_memory.save_chunk("user", "temp", "t", "snippets")
        await client.delete(f"/api/user-memory/chunk/{h}")
        chunks = await user_memory.browse("user")
        assert len(chunks) == 0


@pytest.mark.asyncio
class TestSearch:
    async def test_search_returns_results(self, client, user_memory):
        await user_memory.save_chunk("user", "python async tutorial", "async")
        resp = await client.get("/api/user-memory/search?q=python")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "query" in data
        assert data["query"] == "python"
        assert len(data["results"]) >= 1

    async def test_search_no_match(self, client, user_memory):
        await user_memory.save_chunk("user", "hello world", "hw")
        resp = await client.get("/api/user-memory/search?q=zzzznonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []

    async def test_search_result_shape(self, client, user_memory):
        await user_memory.save_chunk("user", "test content", "test")
        resp = await client.get("/api/user-memory/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        if data["results"]:
            chunk = data["results"][0]
            assert "hash" in chunk
            assert "collection" in chunk
            assert "title" in chunk
            assert "content" in chunk


@pytest.mark.asyncio
class TestAgentSearch:
    async def test_agent_search_requires_permission(self, client, user_memory):
        resp = await client.get(
            "/api/user-memory/agent-search?q=test&agent_name=unauthorized-agent",
        )
        assert resp.status_code == 403
        assert "error" in resp.json()

    async def test_agent_search_with_permission(self, client, user_memory):
        """Grant an agent can_read_user_memory and verify 200."""
        config = client._transport.app.state.config
        new_agent = {"name": "memory-agent", "host": "127.0.0.1", "can_read_user_memory": True}
        config.agents.append(new_agent)
        resp = await client.get(
            "/api/user-memory/agent-search?q=test&agent_name=memory-agent",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["agent"] == "memory-agent"

    async def test_agent_search_missing_agent(self, client, user_memory):
        resp = await client.get(
            "/api/user-memory/agent-search?q=test&agent_name=no-such-agent",
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestListCollections:
    async def test_empty_collections(self, client, user_memory):
        resp = await client.get("/api/user-memory/collections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["collections"] == []

    async def test_collections_after_save(self, client, user_memory):
        await user_memory.save_chunk("user", "a", "a", "snippets")
        await user_memory.save_chunk("user", "b", "b", "journal")
        resp = await client.get("/api/user-memory/collections")
        assert resp.status_code == 200
        data = resp.json()
        assert sorted(data["collections"]) == ["journal", "snippets"]


@pytest.mark.asyncio
class TestMigrate:
    async def test_migrate_unreachable_taosmd(self, client, user_memory):
        with patch.object(
            client._transport.app.state.http_client,
            "get",
            side_effect=ConnectionError("taosmd down"),
        ):
            resp = await client.post("/api/user-memory/migrate")
        assert resp.status_code == 503
        assert resp.json()["error"] == "taosmd unreachable"

    async def test_migrate_unhealthy_taosmd(self, client, user_memory):
        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        with patch.object(
            client._transport.app.state.http_client,
            "get",
            return_value=mock_resp,
        ):
            resp = await client.post("/api/user-memory/migrate")
        assert resp.status_code == 503
        assert resp.json()["error"] == "taosmd not healthy"

    # migrate with live taosmd is skipped: requires a real taosmd service
