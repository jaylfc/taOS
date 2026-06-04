import pytest


class TestBranchesRoute:
    @pytest.mark.asyncio
    async def test_branches_returns_list_and_current(self, client, monkeypatch):
        import tinyagentos.routes.settings as s

        async def fake_lsremote(project_dir):
            return ["master", "dev"]

        async def fake_current(store, project_dir):
            return "master"

        monkeypatch.setattr(s, "_remote_branches", fake_lsremote, raising=False)
        monkeypatch.setattr(s, "resolve_tracked_branch", fake_current, raising=False)

        r = await client.get("/api/settings/branches")
        assert r.status_code == 200
        body = r.json()
        assert set(body["branches"]) == {"master", "dev"}
        assert body["current"] == "master"
