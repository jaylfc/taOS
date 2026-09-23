import pytest
import pytest_asyncio
from tinyagentos.skills import SkillStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = SkillStore(tmp_path / "skills.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_defaults_seeded(store):
    skills = await store.list_skills()
    assert len(skills) >= 5
    ids = [s["id"] for s in skills]
    assert "memory_search" in ids
    assert "file_read" in ids
    assert "web_search" in ids


@pytest.mark.asyncio
async def test_get_skill(store):
    skill = await store.get_skill("memory_search")
    assert skill is not None
    assert skill["name"] == "Memory Search"
    assert skill["category"] == "search"


@pytest.mark.asyncio
async def test_list_by_category(store):
    search_skills = await store.list_skills(category="search")
    assert all(s["category"] == "search" for s in search_skills)


@pytest.mark.asyncio
async def test_compatibility(store):
    skill = await store.get_skill("memory_search")
    assert store.is_compatible(skill, "smolagents") in ("native", "adapter")
    assert store.is_compatible(skill, "nonexistent") == "unsupported"


@pytest.mark.asyncio
async def test_assign_unassign(store):
    await store.assign_skill("agent-1", "memory_search")
    skills = await store.get_agent_skills("agent-1")
    assert len(skills) == 1
    assert skills[0]["id"] == "memory_search"

    await store.unassign_skill("agent-1", "memory_search")
    skills = await store.get_agent_skills("agent-1")
    assert len(skills) == 0


@pytest.mark.asyncio
async def test_compatible_skills_for_framework(store):
    smol_skills = await store.get_compatible_skills("smolagents")
    assert len(smol_skills) > 0
    for s in smol_skills:
        assert store.is_compatible(s, "smolagents") != "unsupported"


@pytest.mark.asyncio
async def test_seeded_skills_match_implementations(store):
    from tinyagentos.routes.skill_exec import SKILL_IMPLEMENTATIONS

    cursor = await store._db.execute(
        "SELECT id FROM skills WHERE install_method = 'builtin'"
    )
    rows = await cursor.fetchall()
    builtin_ids = {row[0] for row in rows}
    impl_ids = set(SKILL_IMPLEMENTATIONS.keys())
    assert builtin_ids == impl_ids
