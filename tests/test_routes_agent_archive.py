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
