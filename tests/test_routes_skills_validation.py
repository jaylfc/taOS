"""Test skill assignment validation in tinyagentos/routes/skills.py

These tests verify that the /api/agents/{agent_id}/skills POST endpoint validates
skill_id against the seeded skill implementations and rejects non-existent skills.
"""

import pytest


class TestSkillAssignmentValidation:
    async def _init_real_skills(self, app):
        store = app.state.skills
        if store._db is None:
            await store.init()

    @pytest.mark.asyncio
    async def test_assign_existing_skill_returns_200(self, client, app):
        """A seeded skill_id should be accepted and return 200."""
        await self._init_real_skills(app)

        resp = await client.post(
            "/api/agents/test-agent/skills",
            json={"skill_id": "memory_search"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"ok": True}

    @pytest.mark.asyncio
    async def test_assign_nonexistent_skill_returns_404(self, client, app):
        """A non-existent skill_id should be rejected with 404."""
        await self._init_real_skills(app)

        resp = await client.post(
            "/api/agents/test-agent/skills",
            json={"skill_id": "does-not-exist"}
        )
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body
        assert "Skill not found" in body["error"]

    @pytest.mark.asyncio
    async def test_assign_nonexistent_skill_does_not_create_row(self, client, app):
        """When a non-existent skill_id is submitted, no agent_skills row should be written."""
        await self._init_real_skills(app)

        # First assign a valid skill to get a baseline
        await client.post(
            "/api/agents/test-agent/skills",
            json={"skill_id": "memory_search"}
        )

        # Verify the valid skill was assigned
        store = app.state.skills
        skills = await store.get_agent_skills("test-agent")
        assert len(skills) == 1
        assert skills[0]["id"] == "memory_search"

        # Try to assign a non-existent skill
        resp = await client.post(
            "/api/agents/test-agent/skills",
            json={"skill_id": "does-not-exist"}
        )
        assert resp.status_code == 404

        # Verify no new row was added
        skills = await store.get_agent_skills("test-agent")
        assert len(skills) == 1
        assert skills[0]["id"] == "memory_search"