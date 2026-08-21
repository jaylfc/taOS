import pytest
import yaml
from tinyagentos.config import load_config, save_config_locked


@pytest.mark.asyncio
class TestConfigPage:
    async def test_get_config_api(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "yaml" in data
        parsed = yaml.safe_load(data["yaml"])
        assert parsed["server"]["port"] == 6969

    async def test_save_valid_config(self, client, tmp_data_dir):
        new_yaml = yaml.dump({
            "server": {"host": "0.0.0.0", "port": 9999},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [],
            "metrics": {"poll_interval": 60, "retention_days": 7},
        })
        resp = await client.put("/api/config", json={"yaml": new_yaml})
        assert resp.status_code == 200
        config = load_config(tmp_data_dir / "config.yaml")
        assert config.server["port"] == 9999

    async def test_save_config_round_trips_lora_ingest_proxy_url(
        self, client, tmp_data_dir
    ):
        """GET returns the key, so PUTting that same YAML back must keep it.

        The config editor is a read-edit-write loop: any field the PUT handler
        forgets to rebuild is silently wiped the first time the user saves an
        unrelated setting.
        """
        new_yaml = yaml.dump({
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [],
            "metrics": {"poll_interval": 60, "retention_days": 7},
            "lora_ingest_proxy_url": "http://proxy.example:3128",
        })
        resp = await client.put("/api/config", json={"yaml": new_yaml})
        assert resp.status_code == 200

        config = load_config(tmp_data_dir / "config.yaml")
        assert config.lora_ingest_proxy_url == "http://proxy.example:3128"

        # And it survives the next read of the API surface.
        resp = await client.get("/api/config")
        parsed = yaml.safe_load(resp.json()["yaml"])
        assert parsed["lora_ingest_proxy_url"] == "http://proxy.example:3128"

    async def test_save_invalid_yaml_fails(self, client):
        resp = await client.put("/api/config", json={"yaml": ": : : bad [["})
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_save_invalid_config_fails(self, client):
        bad_config = yaml.dump({
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [{"name": "bad", "type": "unsupported", "url": "http://x"}],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        })
        resp = await client.put("/api/config", json={"yaml": bad_config})
        assert resp.status_code == 400

    async def test_validate_only(self, client):
        valid_config = yaml.dump({
            "server": {"host": "0.0.0.0", "port": 6969},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [],
            "metrics": {"poll_interval": 30, "retention_days": 30},
        })
        resp = await client.put("/api/config?validate_only=true", json={"yaml": valid_config})
        assert resp.status_code == 200
        assert resp.json()["status"] == "valid"

    async def test_storage_api(self, client):
        resp = await client.get("/api/settings/storage")
        assert resp.status_code == 200
        data = resp.json()
        assert "storage" in data
        assert isinstance(data["storage"], list)
        assert len(data["storage"]) >= 2
        for item in data["storage"]:
            assert "label" in item
            assert "path" in item
            assert "size" in item

    async def test_save_platform_settings(self, client, tmp_data_dir):
        resp = await client.put("/api/settings/platform", json={
            "poll_interval": 120,
            "retention_days": 14,
            "catalog_repo": "",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"
        config = load_config(tmp_data_dir / "config.yaml")
        assert config.metrics["poll_interval"] == 120
        assert config.metrics["retention_days"] == 14

    async def test_save_config_round_trips_archive(self, client, app):
        app.state.config.archive = {"target": "path:/tmp/archive"}
        try:
            resp = await client.get("/api/config")
            data = yaml.safe_load(resp.json()["yaml"])
            data["server"]["port"] = 9999
            resp = await client.put("/api/config", json={"yaml": yaml.dump(data)})
            assert resp.status_code == 200, resp.json()
            assert app.state.config.archive == {"target": "path:/tmp/archive"}
            resp = await client.get("/api/config")
            round_tripped = yaml.safe_load(resp.json()["yaml"])
            assert round_tripped["archive"] == {"target": "path:/tmp/archive"}
        finally:
            app.state.config.archive = {"target": "pool:"}

    async def test_save_config_round_trips_archived_agents(self, client, app):
        app.state.config.archived_agents = [{"name": "old-agent", "status": "archived"}]
        try:
            resp = await client.get("/api/config")
            data = yaml.safe_load(resp.json()["yaml"])
            data["server"]["port"] = 9999
            resp = await client.put("/api/config", json={"yaml": yaml.dump(data)})
            assert resp.status_code == 200, resp.json()
            assert app.state.config.archived_agents == [{"name": "old-agent", "status": "archived"}]
            resp = await client.get("/api/config")
            round_tripped = yaml.safe_load(resp.json()["yaml"])
            assert round_tripped["archived_agents"] == [{"name": "old-agent", "status": "archived"}]
        finally:
            app.state.config.archived_agents = []

    async def test_save_config_round_trips_github_app_id(self, client, app):
        app.state.config.github_app_id = "123456"
        try:
            resp = await client.get("/api/config")
            data = yaml.safe_load(resp.json()["yaml"])
            data["server"]["port"] = 9999
            resp = await client.put("/api/config", json={"yaml": yaml.dump(data)})
            assert resp.status_code == 200, resp.json()
            assert app.state.config.github_app_id == "123456"
            resp = await client.get("/api/config")
            round_tripped = yaml.safe_load(resp.json()["yaml"])
            assert round_tripped["github_app_id"] == "123456"
        finally:
            app.state.config.github_app_id = ""

    async def test_save_config_preserves_all_to_dict_keys(self, client, app):
        config = app.state.config
        config.archive = {"target": "path:/tmp/archive"}
        config.archived_agents = [{"name": "old-agent"}]
        config.github_app_id = "123456"
        config.webhooks = [{"url": "http://example.com/hook", "type": "generic"}]
        config.memory_url = "http://localhost:9999"
        config.taosmd_dir = "/srv/taosmd"
        config.taosmd_restart_cmd = "systemctl restart taosmd"
        expected_keys = set(config.to_dict().keys())
        try:
            resp = await client.get("/api/config")
            data = yaml.safe_load(resp.json()["yaml"])
            data["server"]["port"] = 8888
            resp = await client.put("/api/config", json={"yaml": yaml.dump(data)})
            assert resp.status_code == 200, resp.json()
            resp = await client.get("/api/config")
            round_tripped = yaml.safe_load(resp.json()["yaml"])
            missing = sorted(expected_keys - set(round_tripped.keys()))
            assert not missing, f"PUT /api/config dropped keys: {missing}"
        finally:
            app.state.config.archive = {"target": "pool:"}
            app.state.config.archived_agents = []
            app.state.config.github_app_id = ""
            app.state.config.webhooks = []
            app.state.config.memory_url = "http://localhost:7900"
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

