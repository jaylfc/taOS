from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.mcp.permissions import check_permission
from tinyagentos.mcp.registry import MCPServerStore


@pytest.mark.asyncio
class TestMCPServerPermissions:
    async def _init_store(self, tmp_path):
        db = tmp_path / "mcp.db"
        store = MCPServerStore(db)
        await store.init()
        return store

    async def test_granted_agent_has_access(self, tmp_path):
        store = await self._init_store(tmp_path)
        await store.register_server("srv-1", "1.0", "stdio")
        await store.add_attachment("srv-1", "agent", "agent-a", allowed_tools=["read"])
        result = await check_permission(store, "srv-1", "agent-a", [])
        assert result.allowed is True

    async def test_non_grantee_denied(self, tmp_path):
        store = await self._init_store(tmp_path)
        await store.register_server("srv-1", "1.0", "stdio")
        await store.add_attachment("srv-1", "agent", "agent-a", allowed_tools=["read"])
        result = await check_permission(store, "srv-1", "agent-b", [])
        assert result.allowed is False

    async def test_revoke_removes_access(self, tmp_path):
        store = await self._init_store(tmp_path)
        await store.register_server("srv-1", "1.0", "stdio")
        await store.add_attachment("srv-1", "agent", "agent-a", allowed_tools=["read"])
        result = await check_permission(store, "srv-1", "agent-a", [])
        assert result.allowed is True
        attachments = await store.list_attachments("srv-1")
        await store.delete_attachment(attachments[0]["id"])
        result = await check_permission(store, "srv-1", "agent-a", [])
        assert result.allowed is False
