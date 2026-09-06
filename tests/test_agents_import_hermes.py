"""Tests for POST /api/agents/import: Hermes profile-bundle import."""
import asyncio
import io

import pytest


def _bundle(content=b"PK\x03\x04 fake-bundle", filename="profile.zip"):
    return {"bundle": (filename, io.BytesIO(content), "application/zip")}


@pytest.mark.asyncio
class TestHermesImportValidation:
    async def test_non_hermes_framework_rejected(self, client):
        resp = await client.post(
            "/api/agents/import",
            data={"framework": "smolagents", "name": "x"},
            files=_bundle(),
        )
        assert resp.status_code == 400
        assert "hermes" in resp.json()["error"].lower()

    async def test_missing_bundle_rejected(self, client):
        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "x"},
        )
        assert resp.status_code == 400
        assert "bundle" in resp.json()["error"].lower()

    async def test_bad_extension_rejected(self, client):
        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "x"},
            files=_bundle(filename="profile.txt"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]

    async def test_empty_bundle_rejected(self, client):
        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "x"},
            files=_bundle(content=b""),
        )
        assert resp.status_code == 400
        assert "empty" in resp.json()["error"].lower()

    async def test_oversize_bundle_rejected(self, client, monkeypatch):
        from tinyagentos.routes import agent_import
        monkeypatch.setattr(agent_import, "MAX_BUNDLE_BYTES", 8)
        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "x"},
            files=_bundle(content=b"way too many bytes here"),
        )
        assert resp.status_code == 400
        assert "limit" in resp.json()["error"].lower()

    async def test_invalid_name_rejected(self, client):
        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "   "},
            files=_bundle(),
        )
        assert resp.status_code == 400

    async def test_bad_secrets_json_rejected(self, client):
        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "x", "secrets": "not-json"},
            files=_bundle(),
        )
        assert resp.status_code == 400
        assert "secrets" in resp.json()["error"].lower()


@pytest.mark.asyncio
class TestHermesImportHappyPath:
    async def test_import_runs_profile_import_and_reads_back_persona(
        self, client, app, monkeypatch
    ):
        # Mock the deploy so no real container is created.
        async def fake_deploy(req):
            assert req.framework == "hermes"
            return {
                "success": True, "name": req.name, "ip": "10.0.0.88",
                "llm_key": "sk-imported", "steps": ["deployment_complete"],
                "container": f"taos-agent-{req.name}",
            }
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        pushed = {}

        async def fake_push_file(container, src, dst):
            pushed["container"] = container
            pushed["dst"] = dst
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        calls = []

        async def fake_exec(container, cmd, timeout=None):
            calls.append(cmd)
            # binary probe
            if cmd[:1] == ["test"]:
                return (0, "") if cmd[-1] == "/usr/local/bin/hermes" else (1, "")
            # `hermes profile import <path> --name <slug>`
            if "profile" in cmd and "import" in cmd:
                return 0, "imported profile ok"
            # `hermes profile use <slug>` + `hermes gateway restart`
            if "profile" in cmd and "use" in cmd:
                return 0, "switched"
            if "gateway" in cmd and "restart" in cmd:
                return 0, "restarted"
            # persona readback: SOUL.md
            if cmd[:1] == ["cat"] and cmd[-1].endswith("SOUL.md"):
                return 0, "You are an imported soul."
            # persona readback: config.yaml
            if cmd[:1] == ["cat"] and cmd[-1].endswith("config.yaml"):
                return 0, "default_model: nous/hermes-3\n"
            return 1, ""
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={
                "framework": "hermes",
                "name": "imported-hermes",
                "secrets": '{"OPENROUTER_API_KEY": "sk-or-xyz"}',
            },
            files=_bundle(),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "importing"
        slug = body["name"]
        assert slug == "imported-hermes"

        # Let the background task run.
        for _ in range(20):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break

        task = app.state.deploy_tasks.get(slug)
        assert task is not None, "background import task should have recorded a result"
        assert task["status"] == "success", task

        # `hermes profile import <pushed-path> --name <slug>` was invoked: the
        # pushed path AND an explicit --name (never the bundle's own, possibly
        # `default`, name which hermes refuses to import).
        import_calls = [c for c in calls if "profile" in c and "import" in c]
        assert import_calls, "hermes profile import should have run"
        assert pushed["dst"] in import_calls[0], "import should target the pushed bundle path"
        assert "--name" in import_calls[0], "import must pass --name to avoid the forbidden 'default' name"
        assert slug in import_calls[0], "import should name the profile after the agent slug"
        # The imported profile is made the sticky default so the agent runs it.
        assert any("profile" in c and "use" in c and slug in c for c in calls), (
            "import should `hermes profile use <slug>` to activate the imported profile"
        )

        # Persona readback populated soul_md + model on the agent record.
        detail = await client.get(f"/api/agents/{slug}")
        agent = detail.json()
        assert agent["status"] == "running"
        assert agent["soul_md"] == "You are an imported soul."
        assert agent["model"] == "nous/hermes-3"
        assert agent["llm_key"] == "sk-imported"

        # The user-supplied secret was granted to the agent (same path deploy uses).
        granted = await app.state.secrets.get_agent_secrets(slug)
        names = {s["name"] for s in granted}
        assert f"{slug}-OPENROUTER_API_KEY" in names

    async def test_gateway_restart_nonzero_still_succeeds(self, client, app, monkeypatch):
        """A non-zero `hermes gateway restart` must NOT fail an otherwise good
        import (the profile is imported + set sticky); it is logged, not fatal."""
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.90",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            if cmd[:1] == ["test"]:
                return (0, "") if cmd[-1] == "/usr/local/bin/hermes" else (1, "")
            if "profile" in cmd and "import" in cmd:
                return 0, "ok"
            if "profile" in cmd and "use" in cmd:
                return 0, "switched"
            if "gateway" in cmd and "restart" in cmd:
                return 1, "gateway restart failed"  # non-zero, but not fatal
            if cmd[:1] == ["cat"]:
                return 1, ""
            return 1, ""
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "restart-flaky"},
            files=_bundle(),
        )
        assert resp.status_code == 202
        slug = resp.json()["name"]
        for _ in range(20):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task

    async def test_failed_profile_import_surfaces_error(self, client, app, monkeypatch):
        async def fake_deploy(req):
            return {
                "success": True, "name": req.name, "ip": "10.0.0.89",
                "llm_key": None, "steps": ["deployment_complete"],
                "container": f"taos-agent-{req.name}",
            }
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            if cmd[:1] == ["test"]:
                return (0, "") if cmd[-1] == "/usr/local/bin/hermes" else (1, "")
            if "profile" in cmd and "import" in cmd:
                return 2, "boom: corrupt bundle"
            return 1, ""
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "broken-import"},
            files=_bundle(),
        )
        assert resp.status_code == 202
        slug = resp.json()["name"]

        for _ in range(20):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break

        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "failed"
        assert "profile import" in task["error"].lower()

        detail = await client.get(f"/api/agents/{slug}")
        assert detail.json()["status"] == "failed"


@pytest.mark.asyncio
class TestJsonImportStillWorks:
    async def test_json_config_import_unchanged(self, client):
        payload = {
            "version": 1,
            "agent": {"name": "json-imported", "host": "10.0.0.5", "color": "#fff"},
            "channels": [],
            "groups": [],
        }
        resp = await client.post("/api/agents/import", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "imported"
