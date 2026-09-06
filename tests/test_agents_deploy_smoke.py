import pytest
from unittest.mock import AsyncMock, MagicMock


def _install_mock_archive(app):
    """Attach a mock archive to app.state and return it."""
    archive = MagicMock()
    archive.record = AsyncMock(return_value=None)
    archive.query = AsyncMock(return_value=[])
    app.state.archive = archive
    return archive


@pytest.mark.asyncio
class TestDeploySmokeCheck:
    async def test_deploy_response_includes_archive_smoke_ok_true(self, client, app):
        archive = _install_mock_archive(app)
        archive.query = AsyncMock(return_value=[{"event_type": "agent_deployed"}])

        resp = await client.post("/api/agents/deploy", json={"name": "Atlas", "framework": "none"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["archive_smoke_ok"] is True
        archive.record.assert_called_once()
        kwargs = archive.record.call_args.kwargs
        assert kwargs["event_type"] == "agent_deployed"
        assert kwargs["agent_name"] == "atlas"

    async def test_deploy_response_archive_smoke_false_on_record_failure(self, client, app):
        archive = _install_mock_archive(app)
        archive.record = AsyncMock(side_effect=RuntimeError("archive down"))

        resp = await client.post("/api/agents/deploy", json={"name": "Atlas2", "framework": "none"})

        assert resp.status_code == 200
        assert resp.json()["archive_smoke_ok"] is False

    async def test_deploy_response_archive_smoke_false_when_query_returns_empty(self, client, app):
        archive = _install_mock_archive(app)
        # record succeeds but query returns nothing
        archive.query = AsyncMock(return_value=[])

        resp = await client.post("/api/agents/deploy", json={"name": "Atlas3", "framework": "none"})

        assert resp.status_code == 200
        assert resp.json()["archive_smoke_ok"] is False
