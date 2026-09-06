"""Tests for recycle-bin API routes."""
from __future__ import annotations

import base64

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRASH_LIST_OUTPUT = (
    "2026-04-15 10:30:00 /root/workspace/foo.txt\n"
    "2026-04-14 08:15:22 /root/workspace/bar.py\n"
    "2026-04-13 19:00:01 /root/notes/old.md\n"
)


def _encode_id(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


# ---------------------------------------------------------------------------
# GET /api/agents/{name}/recycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestListAgentRecycle:
    async def test_list_parses_output(self, client):
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(0, TRASH_LIST_OUTPUT)),
        ):
            resp = await client.get("/api/agents/test-agent/recycle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert data["status"] == "ok"
        assert len(data["items"]) == 3
        item = data["items"][0]
        assert item["original_path"] == "/root/workspace/foo.txt"
        assert item["deleted_at"] == "2026-04-15T10:30:00Z"
        assert item["size_bytes"] is None
        assert item["id"] == _encode_id("/root/workspace/foo.txt")

    async def test_empty_output_returns_empty_items(self, client):
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(0, "")),
        ):
            resp = await client.get("/api/agents/test-agent/recycle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["status"] == "ok"

    async def test_container_offline_nonzero_rc(self, client):
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(1, "Error: container not running")),
        ):
            resp = await client.get("/api/agents/test-agent/recycle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "container_offline"
        assert data["items"] == []

    async def test_container_offline_exception(self, client):
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            resp = await client.get("/api/agents/test-agent/recycle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "container_offline"
        assert data["items"] == []

    async def test_unknown_agent_returns_404(self, client):
        resp = await client.get("/api/agents/ghost-agent/recycle")
        assert resp.status_code == 404

    async def test_id_is_stable_base64url_of_path(self, client):
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(0, "2026-04-15 10:30:00 /root/workspace/foo.txt\n")),
        ):
            resp = await client.get("/api/agents/test-agent/recycle")
        item = resp.json()["items"][0]
        decoded = base64.urlsafe_b64decode(item["id"] + "==").decode()
        assert decoded == "/root/workspace/foo.txt"

    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.get("/api/agents/test-agent/recycle")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/recycle  (aggregated)
# ---------------------------------------------------------------------------

AGENT_A_OUTPUT = "2026-04-15 10:00:00 /root/a.txt\n"
AGENT_B_OUTPUT = "2026-04-16 12:00:00 /root/b.txt\n"


@pytest.mark.asyncio
class TestListAllRecycle:
    async def _inject_second_agent(self, app):
        app.state.config.agents.append(
            {"name": "agent-b", "host": "10.0.0.2", "qmd_index": "b", "color": "#aaaaaa"}
        )

    async def test_aggregates_across_agents(self, client):
        app = client._transport.app
        await self._inject_second_agent(app)

        call_count = 0

        async def _fake_exec(container, cmd, timeout=10):
            nonlocal call_count
            call_count += 1
            if "test-agent" in container:
                return 0, AGENT_A_OUTPUT
            return 0, AGENT_B_OUTPUT

        with patch("tinyagentos.routes.recycle.exec_in_container", new=_fake_exec):
            resp = await client.get("/api/recycle")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert call_count == 2

    async def test_sorted_newest_first(self, client):
        app = client._transport.app
        await self._inject_second_agent(app)

        async def _fake_exec(container, cmd, timeout=10):
            if "test-agent" in container:
                return 0, AGENT_A_OUTPUT  # older: 2026-04-15
            return 0, AGENT_B_OUTPUT  # newer: 2026-04-16

        with patch("tinyagentos.routes.recycle.exec_in_container", new=_fake_exec):
            resp = await client.get("/api/recycle")
        items = resp.json()["items"]
        # Newest-first: agent-b's 2026-04-16 item should be first
        assert items[0]["deleted_at"] > items[1]["deleted_at"]

    async def test_offline_agent_included_as_empty(self, client):
        app = client._transport.app
        await self._inject_second_agent(app)

        async def _fake_exec(container, cmd, timeout=10):
            if "test-agent" in container:
                return 0, AGENT_A_OUTPUT
            return 1, "offline"  # agent-b offline

        with patch("tinyagentos.routes.recycle.exec_in_container", new=_fake_exec):
            resp = await client.get("/api/recycle")
        data = resp.json()
        # Only test-agent's item appears
        assert len(data["items"]) == 1
        assert data["items"][0]["agent_name"] == "test-agent"

    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.get("/api/recycle")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /api/agents/{name}/recycle/restore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRestoreItem:
    async def test_restore_by_id_success(self, client):
        item_id = _encode_id("/root/workspace/foo.txt")
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(0, "")),
        ):
            resp = await client.post(
                "/api/agents/test-agent/recycle/restore",
                json={"id": item_id},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restored"
        assert data["original_path"] == "/root/workspace/foo.txt"

    async def test_restore_by_original_path_success(self, client):
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(0, "")),
        ):
            resp = await client.post(
                "/api/agents/test-agent/recycle/restore",
                json={"original_path": "/root/workspace/bar.py"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

    async def test_restore_invalid_id_returns_400(self, client):
        resp = await client.post(
            "/api/agents/test-agent/recycle/restore",
            json={"id": "!!!not-base64!!!"},
        )
        assert resp.status_code == 400

    async def test_restore_missing_body_returns_400(self, client):
        resp = await client.post(
            "/api/agents/test-agent/recycle/restore",
            json={},
        )
        assert resp.status_code == 400

    async def test_restore_relative_path_returns_400(self, client):
        # A relative path encodes fine but must be rejected for safety
        item_id = _encode_id("relative/path.txt")
        resp = await client.post(
            "/api/agents/test-agent/recycle/restore",
            json={"id": item_id},
        )
        assert resp.status_code == 400

    async def test_restore_unknown_agent_returns_404(self, client):
        item_id = _encode_id("/root/workspace/foo.txt")
        resp = await client.post(
            "/api/agents/ghost-agent/recycle/restore",
            json={"id": item_id},
        )
        assert resp.status_code == 404

    async def test_restore_container_offline_exception_returns_409(self, client):
        item_id = _encode_id("/root/workspace/foo.txt")
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            resp = await client.post(
                "/api/agents/test-agent/recycle/restore",
                json={"id": item_id},
            )
        assert resp.status_code == 409

    async def test_restore_item_not_found_in_trash(self, client):
        item_id = _encode_id("/root/workspace/gone.txt")
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(1, "No files found in trash")),
        ):
            resp = await client.post(
                "/api/agents/test-agent/recycle/restore",
                json={"id": item_id},
            )
        assert resp.status_code == 404

    async def test_unauthenticated_returns_401(self, app):
        item_id = _encode_id("/root/workspace/foo.txt")
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.post(
                "/api/agents/test-agent/recycle/restore",
                json={"id": item_id},
            )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# DELETE /api/agents/{name}/recycle/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPurgeItem:
    async def test_purge_success(self, client):
        item_id = _encode_id("/root/workspace/foo.txt")
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(0, "")),
        ):
            resp = await client.delete(f"/api/agents/test-agent/recycle/{item_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "purged"
        assert data["id"] == item_id

    async def test_purge_invalid_id_returns_400(self, client):
        resp = await client.delete("/api/agents/test-agent/recycle/!!!bad!!!")
        assert resp.status_code == 400

    async def test_purge_relative_path_returns_400(self, client):
        item_id = _encode_id("relative/path.txt")
        resp = await client.delete(f"/api/agents/test-agent/recycle/{item_id}")
        assert resp.status_code == 400

    async def test_purge_unknown_agent_returns_404(self, client):
        item_id = _encode_id("/root/workspace/foo.txt")
        resp = await client.delete(f"/api/agents/ghost-agent/recycle/{item_id}")
        assert resp.status_code == 404

    async def test_purge_container_offline_exception_returns_409(self, client):
        item_id = _encode_id("/root/workspace/foo.txt")
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            resp = await client.delete(f"/api/agents/test-agent/recycle/{item_id}")
        assert resp.status_code == 409

    async def test_purge_command_failure_returns_500(self, client):
        item_id = _encode_id("/root/workspace/foo.txt")
        with patch(
            "tinyagentos.routes.recycle.exec_in_container",
            new=AsyncMock(return_value=(1, "permission denied")),
        ):
            resp = await client.delete(f"/api/agents/test-agent/recycle/{item_id}")
        assert resp.status_code == 500

    async def test_unauthenticated_returns_401(self, app):
        item_id = _encode_id("/root/workspace/foo.txt")
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.delete(f"/api/agents/test-agent/recycle/{item_id}")
        assert resp.status_code in (401, 403)
