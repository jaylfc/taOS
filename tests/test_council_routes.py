import pytest


@pytest.mark.asyncio
class TestCouncilRoutes:
    async def test_list_roles_returns_seeded_roles(self, client, app):
        roles_resp = await client.get("/api/council/roles")
        assert roles_resp.status_code == 200
        roles = roles_resp.json()
        assert len(roles) == 10
        slugs = {r["slug"] for r in roles}
        expected = {
            "coder", "reviewer", "writer", "editor", "summarizer",
            "translator", "researcher", "planner", "critic", "data_analyst",
        }
        assert slugs == expected

    async def test_list_roles_has_expected_fields(self, client, app):
        roles_resp = await client.get("/api/council/roles")
        assert roles_resp.status_code == 200
        roles = roles_resp.json()
        for role in roles:
            assert "slug" in role
            assert "display_name" in role
            assert "description" in role
            assert "gauge_status" in role
            assert "builtin" in role

    async def test_list_members_returns_empty_when_none(self, client, app):
        members_resp = await client.get("/api/council/members")
        assert members_resp.status_code == 200
        assert members_resp.json() == []

    async def test_list_members_returns_added_members(self, client, app):
        store = app.state.council_members
        if store._db is not None:
            await store.close()
        await store.init()
        await store.add_member(
            canonical_id="agent-route-1",
            model_id="kilo-auto/free",
            provider="kilocode",
            roles=[{"role": "coder", "score": 90, "source": "local"}],
            autonomy={"coder": "propose"},
        )

        members_resp = await client.get("/api/council/members")
        assert members_resp.status_code == 200
        members = members_resp.json()
        assert len(members) == 1
        assert members[0]["canonical_id"] == "agent-route-1"
        assert members[0]["roles"] == [{"role": "coder", "score": 90, "source": "local"}]
