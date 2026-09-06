import pytest
import pytest_asyncio
from pathlib import Path

from tinyagentos.relationships import RelationshipManager


@pytest_asyncio.fixture
async def mgr(tmp_path):
    m = RelationshipManager(tmp_path / "rel.db")
    await m.init()
    yield m
    await m.close()


@pytest.mark.asyncio
class TestRelationshipManager:
    async def test_create_group(self, mgr):
        gid = await mgr.create_group("team-alpha", description="Alpha squad", color="#ff0000")
        assert isinstance(gid, int)
        groups = await mgr.list_groups()
        assert len(groups) == 1
        assert groups[0]["name"] == "team-alpha"
        assert groups[0]["color"] == "#ff0000"

    async def test_list_groups_empty(self, mgr):
        groups = await mgr.list_groups()
        assert groups == []

    async def test_update_group(self, mgr):
        gid = await mgr.create_group("old-name")
        await mgr.update_group(gid, name="new-name", description="updated")
        groups = await mgr.list_groups()
        assert groups[0]["name"] == "new-name"
        assert groups[0]["description"] == "updated"

    async def test_delete_group(self, mgr):
        gid = await mgr.create_group("to-delete")
        deleted = await mgr.delete_group(gid)
        assert deleted is True
        groups = await mgr.list_groups()
        assert len(groups) == 0

    async def test_delete_nonexistent_group(self, mgr):
        deleted = await mgr.delete_group(9999)
        assert deleted is False

    async def test_add_member(self, mgr):
        gid = await mgr.create_group("squad")
        await mgr.add_member(gid, "agent-a", role="lead")
        await mgr.add_member(gid, "agent-b")
        groups = await mgr.list_groups()
        members = groups[0]["members"]
        assert len(members) == 2
        names = {m["agent_name"] for m in members}
        assert names == {"agent-a", "agent-b"}

    async def test_remove_member(self, mgr):
        gid = await mgr.create_group("squad")
        await mgr.add_member(gid, "agent-a")
        await mgr.add_member(gid, "agent-b")
        await mgr.remove_member(gid, "agent-a")
        groups = await mgr.list_groups()
        members = groups[0]["members"]
        assert len(members) == 1
        assert members[0]["agent_name"] == "agent-b"

    async def test_get_agent_groups(self, mgr):
        gid1 = await mgr.create_group("team-1")
        gid2 = await mgr.create_group("team-2")
        await mgr.add_member(gid1, "agent-x")
        await mgr.add_member(gid2, "agent-x")
        agent_groups = await mgr.get_agent_groups("agent-x")
        assert len(agent_groups) == 2
        group_names = {g["name"] for g in agent_groups}
        assert group_names == {"team-1", "team-2"}

    async def test_set_permission(self, mgr):
        await mgr.set_permission("agent-a", "agent-b")
        assert await mgr.can_communicate("agent-a", "agent-b") is True
        assert await mgr.can_communicate("agent-b", "agent-a") is False

    async def test_revoke_permission(self, mgr):
        await mgr.set_permission("agent-a", "agent-b")
        await mgr.revoke_permission("agent-a", "agent-b")
        assert await mgr.can_communicate("agent-a", "agent-b") is False

    async def test_get_agent_permissions(self, mgr):
        await mgr.set_permission("agent-a", "agent-b")
        await mgr.set_permission("agent-c", "agent-a")
        perms = await mgr.get_agent_permissions("agent-a")
        assert "agent-b" in perms["can_reach"]
        assert "agent-c" in perms["reachable_by"]

    async def test_duplicate_permission_ignored(self, mgr):
        await mgr.set_permission("agent-a", "agent-b")
        await mgr.set_permission("agent-a", "agent-b")  # should not raise
        perms = await mgr.get_agent_permissions("agent-a")
        assert perms["can_reach"].count("agent-b") == 1

    async def test_delete_group_cascades_members(self, mgr):
        gid = await mgr.create_group("temp")
        await mgr.add_member(gid, "agent-a")
        await mgr.delete_group(gid)
        agent_groups = await mgr.get_agent_groups("agent-a")
        assert len(agent_groups) == 0
