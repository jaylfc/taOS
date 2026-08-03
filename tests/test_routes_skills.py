"""Read-endpoint tests for tinyagentos/routes/skills.py (GET only).

These tests drive the live ASGI app through the ``client`` fixture against the
real ``SkillStore`` (seeded with the builtin skills).  The ``client`` fixture
bypasses the application lifespan, so ``app.state.skills`` is constructed but
never ``init()``-ed; the shared helper below lazily initialises that real
store on first access so every assertion reflects real seeded data rather than
a mock.
"""

import pytest


class TestSkillsRoutes:
    async def _init_real_skills(self, app):
        store = app.state.skills
        if store._db is None:
            await store.init()

    @pytest.mark.asyncio
    async def test_list_skills_returns_collection(self, client, app):
        await self._init_real_skills(app)

        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        body = resp.json()
        assert "skills" in body
        assert isinstance(body["skills"], list)
        assert len(body["skills"]) >= 5
        ids = [s["id"] for s in body["skills"]]
        assert "memory_search" in ids
        assert "file_read" in ids
        assert "web_search" in ids
        for skill in body["skills"]:
            assert isinstance(skill, dict)
            assert "id" in skill
            assert "name" in skill
            assert "category" in skill

    @pytest.mark.asyncio
    async def test_get_skill_known_id_returns_single_item(self, client, app):
        await self._init_real_skills(app)

        resp = await client.get("/api/skills/memory_search")
        assert resp.status_code == 200
        skill = resp.json()
        assert isinstance(skill, dict)
        assert skill["id"] == "memory_search"
        assert skill["name"] == "Memory Search"
        assert skill["category"] == "search"
        assert "tool_schema" in skill
        assert "frameworks" in skill
        assert "installed" in skill

    @pytest.mark.asyncio
    async def test_get_skill_unknown_id_returns_404(self, client, app):
        await self._init_real_skills(app)

        resp = await client.get("/api/skills/does-not-exist-9999")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Skill not found"}

    @pytest.mark.asyncio
    async def test_compatible_skills_for_handled_framework(self, client, app):
        await self._init_real_skills(app)

        resp = await client.get("/api/skills/compatible/smolagents")
        assert resp.status_code == 200
        body = resp.json()
        assert "skills" in body
        assert isinstance(body["skills"], list)
        assert len(body["skills"]) > 0
        for skill in body["skills"]:
            assert isinstance(skill, dict)
            assert "id" in skill
