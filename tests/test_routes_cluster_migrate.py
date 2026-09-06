import pytest


class TestClusterRemotesRead:
    @pytest.mark.asyncio
    async def test_list_remotes_returns_200(self, client, monkeypatch):
        async def fake_remote_list():
            return []

        monkeypatch.setattr(
            "tinyagentos.routes.cluster_migrate.remote_list",
            fake_remote_list,
        )
        resp = await client.get("/api/cluster/remotes")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_remotes_returns_configured_remotes(self, client, monkeypatch):
        fake_remotes = [
            {"name": "host-b", "addr": "https://10.0.0.2:8443", "protocol": "simplestreams"},
            {"name": "host-c", "addr": "https://10.0.0.3:8443", "protocol": "simplestreams"},
        ]

        async def fake_remote_list():
            return fake_remotes

        monkeypatch.setattr(
            "tinyagentos.routes.cluster_migrate.remote_list",
            fake_remote_list,
        )
        resp = await client.get("/api/cluster/remotes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "host-b"
        assert data[0]["addr"] == "https://10.0.0.2:8443"
        assert data[0]["protocol"] == "simplestreams"
