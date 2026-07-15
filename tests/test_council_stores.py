import pytest
import pytest_asyncio
from pathlib import Path

from tinyagentos.council.member_store import MemberStore
from tinyagentos.council.role_registry import RoleRegistry


@pytest_asyncio.fixture
async def role_registry(tmp_path):
    store = RoleRegistry(tmp_path / "council_roles.db")
    await store.init()
    yield store
    await store.close()


@pytest_asyncio.fixture
async def member_store(tmp_path):
    store = MemberStore(tmp_path / "council_members.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestRoleRegistry:
    async def test_seeds_ten_roles(self, role_registry):
        roles = await role_registry.list_roles()
        assert len(roles) == 10
        slugs = {r["slug"] for r in roles}
        expected = {
            "coder", "reviewer", "writer", "editor", "summarizer",
            "translator", "researcher", "planner", "critic", "data_analyst",
        }
        assert slugs == expected

    async def test_get_existing_role(self, role_registry):
        role = await role_registry.get_role("coder")
        assert role is not None
        assert role["display_name"] == "Coder"
        assert role["gauge_status"] == "proven"
        assert role["builtin"] is True

    async def test_get_missing_role_returns_none(self, role_registry):
        role = await role_registry.get_role("nonexistent")
        assert role is None

    async def test_roles_sorted_by_slug(self, role_registry):
        roles = await role_registry.list_roles()
        slugs = [r["slug"] for r in roles]
        assert slugs == sorted(slugs)

    async def test_proven_roles_have_suite_version(self, role_registry):
        roles = await role_registry.list_roles()
        proven = [r for r in roles if r["gauge_status"] == "proven"]
        for r in proven:
            assert r["suite_version"] is not None

    async def test_provisional_roles_have_null_suite_version(self, role_registry):
        roles = await role_registry.list_roles()
        provisional = [r for r in roles if r["gauge_status"] == "provisional"]
        for r in provisional:
            assert r["suite_version"] is None


@pytest.mark.asyncio
class TestMemberStore:
    async def test_add_and_list_member(self, member_store):
        member = await member_store.add_member(
            canonical_id="agent-001",
            model_id="kilo-auto/free",
            provider="kilocode",
            roles=[{"role": "coder", "score": 85, "source": "local"}],
            autonomy={"coder": "propose"},
        )
        assert member["status"] == "active"
        assert member["id"] is not None

        members = await member_store.list_members()
        assert len(members) == 1
        assert members[0]["canonical_id"] == "agent-001"
        assert members[0]["model_id"] == "kilo-auto/free"
        assert members[0]["roles"] == [{"role": "coder", "score": 85, "source": "local"}]
        assert members[0]["autonomy"] == {"coder": "propose"}

    async def test_get_member(self, member_store):
        created = await member_store.add_member(
            canonical_id="agent-002",
            model_id="stepflash:free",
            provider="kilocode",
            roles=[],
            autonomy={},
        )
        fetched = await member_store.get_member(created["id"])
        assert fetched is not None
        assert fetched["canonical_id"] == "agent-002"
        assert fetched["provider"] == "kilocode"

    async def test_get_missing_returns_none(self, member_store):
        assert await member_store.get_member("does-not-exist") is None

    async def test_list_empty(self, member_store):
        assert await member_store.list_members() == []

    async def test_duplicate_canonical_id_rejected(self, member_store):
        await member_store.add_member(
            canonical_id="agent-dup",
            model_id="m1",
            provider="p",
            roles=[],
            autonomy={},
        )
        with pytest.raises(Exception):
            await member_store.add_member(
                canonical_id="agent-dup",
                model_id="m2",
                provider="p",
                roles=[],
                autonomy={},
            )

    async def test_default_status_is_active(self, member_store):
        member = await member_store.add_member(
            canonical_id="agent-003",
            model_id="hy3:free",
            provider="kilocode",
            roles=[],
            autonomy={},
        )
        assert member["status"] == "active"
