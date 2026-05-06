"""install-targets must include targets[] for the resolver-driven Store filter."""
import pytest


class TestInstallTargetsResolverFields:
    @pytest.mark.asyncio
    async def test_each_target_has_targets_list(self, client):
        r = await client.get("/api/cluster/install-targets")
        assert r.status_code == 200
        targets = r.json()
        assert len(targets) >= 1
        for t in targets:
            assert "targets" in t
            assert isinstance(t["targets"], list)
            # At minimum every device has cpu in its targets list.
            assert "cpu" in t["targets"]

    @pytest.mark.asyncio
    async def test_local_target_has_at_least_cpu(self, client):
        r = await client.get("/api/cluster/install-targets")
        local = next(t for t in r.json() if t["name"] == "local")
        assert "cpu" in local["targets"]
