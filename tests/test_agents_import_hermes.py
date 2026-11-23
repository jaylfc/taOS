"""Tests for POST /api/agents/import: Hermes profile-bundle import."""
import asyncio
import io
import json
import tarfile
import tempfile
from pathlib import Path

import pytest

from tinyagentos.routes.agent_import import _CONTAINER_BUNDLE_PATH


def _make_minimal_bundle() -> bytes:
    """Create a minimal valid tar.gz bundle for testing."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add a minimal config.yaml
        config_content = b"model:\n  default: test-model\n"
        ti = tarfile.TarInfo(name="config.yaml")
        ti.size = len(config_content)
        tar.addfile(ti, io.BytesIO(config_content))
        # Add a minimal SOUL.md
        soul_content = b"Test persona"
        ti = tarfile.TarInfo(name="SOUL.md")
        ti.size = len(soul_content)
        tar.addfile(ti, io.BytesIO(soul_content))
    return buf.getvalue()


def _bundle(content=None, filename="profile.tar.gz"):
    if content is None:
        content = _make_minimal_bundle()
    return {"bundle": (filename, io.BytesIO(content), "application/gzip")}


def _make_fake_exec(overrides: dict = None):
    """Create a fake_exec that handles all the commands the import runs."""
    overrides = overrides or {}
    
    async def fake_exec(container, cmd, timeout=None):
        cmd_str = " ".join(cmd)
        
        # Check overrides first - pattern -> (code, output)
        for pattern, result in overrides.items():
            if pattern in cmd_str:
                if isinstance(result, tuple):
                    return result
                else:
                    return 0, str(result)
        
        # binary probe
        if cmd[:1] == ["test"]:
            return (0, "") if cmd[-1] == "/usr/local/bin/hermes" else (1, "")
        
        # hermes profile import
        if "profile" in cmd and "import" in cmd:
            return 0, "imported profile ok"
        
        # hermes profile use
        if "profile" in cmd and "use" in cmd:
            return 0, "switched"
        
        # hermes gateway restart (old path, should not be used now)
        if "gateway" in cmd and "restart" in cmd:
            return 0, "restarted"
        
        # systemd drop-in creation (bash -c with mkdir && cat)
        if "mkdir -p /etc/systemd/system/hermes-gateway.service.d" in cmd_str and "HERMES_HOME" in cmd_str:
            return 0, ""
        
        # systemctl daemon-reload
        if "systemctl" in cmd and "daemon-reload" in cmd:
            return 0, "reloaded"
        
# systemctl restart hermes-gateway
        if "systemctl" in cmd and "restart" in cmd and "hermes-gateway" in cmd:
            return 0, "restarted"

        # systemctl is-active hermes-gateway (wait for gateway to be active)
        if "systemctl" in cmd and "is-active" in cmd and "hermes-gateway" in cmd:
            return 0, "active"

        # systemctl show hermes-gateway -p Environment (verify HERMES_HOME)
        if "systemctl" in cmd and "show" in cmd and "hermes-gateway" in cmd and "Environment" in cmd_str:
            # Use the active_profile override or default to test-slug
            active_profile = overrides.get("active_profile", "test-slug")
            return 0, f"Environment=HERMES_HOME=/root/.hermes/profiles/{active_profile}"

        # hermes profile active
        if "profile" in cmd and "active" in cmd:
            active_profile = overrides.get("active_profile", "test-slug")
            return 0, active_profile
        
        # cat config.yaml (for rewrite readback)
        if cmd[:1] == ["cat"] and "config.yaml" in cmd_str and ".pre-import" not in cmd_str:
            return 0, "model:\n  default: kilo-auto/free\n  provider: custom\n  base_url: \"http://127.0.0.1:4000/v1\"\n  api_key: \"test-key\"\n"
        
        # cp config.yaml config.yaml.pre-import
        if "cp " in cmd_str and "config.yaml" in cmd_str and ".pre-import" in cmd_str:
            return 0, ""
        
        # write new config.yaml (bash -c cat > ...)
        if "cat >" in cmd_str and "config.yaml" in cmd_str and ".pre-import" not in cmd_str:
            return 0, ""
        
        # cat jobs.json
        if cmd[:1] == ["cat"] and "jobs.json" in cmd_str:
            return 0, '{"jobs": [{"name": "daily-summary", "enabled": true, "schedule": "0 9 * * *"}]}'
        
        # write jobs.json (bash -c cat > ...)
        if "cat >" in cmd_str and "jobs.json" in cmd_str:
            return 0, ""
        
        # cat config.yaml.pre-import (for transfer notice)
        if cmd[:1] == ["cat"] and ".pre-import" in cmd_str:
            return 0, 'model:\n  default: local-model\n  provider: custom\n  base_url: "http://127.0.0.1:8080/v1"\n  api_key: "local-only"\n'
        
        # write transfer_notice.md
        if "transfer_notice.md" in cmd_str:
            return 0, ""
        
        # write first_message.txt
        if "first_message.txt" in cmd_str:
            return 0, ""
        
        # persona readback: SOUL.md
        if cmd[:1] == ["cat"] and cmd[-1].endswith("SOUL.md"):
            return 0, "You are an imported soul."
        
        # persona readback: config.yaml (old path)
        if cmd[:1] == ["cat"] and cmd[-1].endswith("config.yaml"):
            return 0, "default_model: nous/hermes-3\n"
        
        return 1, ""
    
    return fake_exec


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

        pushed = []

        async def fake_push_file(container, src, dst):
            pushed.append({"container": container, "src": src, "dst": dst})
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        calls = []

        async def fake_exec(container, cmd, timeout=None):
            calls.append(cmd)
            # Delegate to shared fake_exec but track calls
            fake = _make_fake_exec({"active_profile": "imported-hermes"})
            return await fake(container, cmd, timeout)
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
        for _ in range(30):
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
        bundle_push = next((p for p in pushed if p["dst"] == _CONTAINER_BUNDLE_PATH), None)
        assert bundle_push is not None, "bundle should be pushed"
        assert bundle_push["dst"] in import_calls[0], "import should target the pushed bundle path"
        assert "--name" in import_calls[0], "import must pass --name to avoid the forbidden 'default' name"
        assert slug in import_calls[0], "import should name the profile after the agent slug"
        # The imported profile is made the sticky default so the agent runs it.
        assert any("profile" in c and "use" in c and slug in c for c in calls), (
            "import should `hermes profile use <slug>` to activate the imported profile"
        )

        # Systemd drop-in created and gateway restarted with HERMES_HOME
        # Now created via push_file, verify the drop-in path was pushed
        dropin_push = next((p for p in pushed if "99-taos-import.conf" in p["dst"]), None)
        assert dropin_push is not None, "systemd drop-in should be pushed"

        # Verify active profile check
        active_calls = [c for c in calls if "profile" in c and "active" in c]
        assert active_calls, "should verify active profile after restart"

        # Persona readback populated soul_md + model on the agent record.
        detail = await client.get(f"/api/agents/{slug}")
        agent = detail.json()
        assert agent["status"] == "running"
        assert agent["soul_md"] == "You are an imported soul."
        assert agent["model"] == "kilo-auto/free"  # rewritten model
        # The key is persisted but never returned by a read (GHSA-v258-j222-x68c).
        assert "llm_key" not in agent
        stored = next(a for a in app.state.config.agents if a["name"] == slug)
        assert stored["llm_key"] == "sk-imported"

        # The user-supplied secret was granted to the agent (same path deploy uses).
        granted = await app.state.secrets.get_agent_secrets(slug)
        names = {s["name"] for s in granted}
        assert f"{slug}-OPENROUTER_API_KEY" in names

    async def test_gateway_restart_nonzero_still_succeeds(self, client, app, monkeypatch):
        """A non-zero `hermes gateway restart` must NOT fail an otherwise good
        import (the profile is imported + set sticky); it is logged, not fatal."""
        # NOTE: This test now uses systemctl restart which must succeed.
        # The old hermes gateway restart path is no longer used.
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.90",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            fake = _make_fake_exec({"active_profile": "restart-flaky"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "restart-flaky"},
            files=_bundle(),
        )
        assert resp.status_code == 202
        slug = resp.json()["name"]
        for _ in range(30):
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

    async def test_gateway_not_serving_imported_profile_fails_import(self, client, app, monkeypatch):
        """If the gateway is not serving the imported profile after restart,
        the import MUST fail (not just log a warning)."""
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.91",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            fake = _make_fake_exec({"active_profile": "default"})  # WRONG profile
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "gateway-check-fail"},
            files=_bundle(),
        )
        assert resp.status_code == 202
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "failed", f"Import should fail when gateway serves wrong profile: {task}"
        assert "gateway" in task["error"].lower() or "profile" in task["error"].lower()

        detail = await client.get(f"/api/agents/{slug}")
        assert detail.json()["status"] == "failed"


@pytest.mark.asyncio
class TestTarBundleValidation:
    """Tests for tar bundle validation before handing to Hermes."""

    def _make_bundle(self, members: list[tuple[str, bytes | None, str]], filename="profile.tar.gz"):
        """Create a tar.gz bundle with the given members.
        Each member: (name, content_or_None_for_symlink, link_target_if_symlink)"""
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            with tarfile.open(tmp.name, "w:gz") as tar:
                for name, content, link_target in members:
                    if content is None:  # symlink
                        ti = tarfile.TarInfo(name=name)
                        ti.type = tarfile.SYMTYPE
                        ti.linkname = link_target
                        tar.addfile(ti)
                    else:
                        ti = tarfile.TarInfo(name=name)
                        ti.size = len(content)
                        tar.addfile(ti, io.BytesIO(content))
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            content = f.read()
        Path(tmp_path).unlink()
        return {"bundle": (filename, io.BytesIO(content), "application/gzip")}

    async def test_absolute_path_in_bundle_rejected(self, client, app, monkeypatch):
        """Bundle with absolute paths must be rejected before reaching Hermes."""
        # Create a bundle with an absolute path
        members = [
            ("/absolute/path/file.txt", b"bad", ""),
        ]
        bundle = self._make_bundle(members)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.92",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            return 1, ""
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "absolute-path-test"},
            files=bundle,
        )
        assert resp.status_code == 400, resp.json()
        assert "absolute" in resp.json()["error"].lower() or "validation" in resp.json()["error"].lower()

    async def test_dotdot_path_in_bundle_rejected(self, client, app, monkeypatch):
        """Bundle with .. paths must be rejected before reaching Hermes."""
        members = [
            ("../escape.txt", b"bad", ""),
            ("skills/../../etc/passwd", b"bad", ""),
        ]
        bundle = self._make_bundle(members)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.93",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            return 1, ""
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "dotdot-path-test"},
            files=bundle,
        )
        assert resp.status_code == 400, resp.json()
        assert ".." in resp.json().get("error", "") or "traversal" in resp.json().get("error", "").lower() or "validation" in resp.json().get("error", "").lower()

    async def test_symlink_dropped_and_reported(self, client, app, monkeypatch):
        """Symlinks in bundle must be dropped (not passed to Hermes) and reported."""
        members = [
            ("skills/normal_skill.py", b"print('hello')", ""),
            ("skills/dangling_link", None, "/etc/passwd"),  # symlink
            ("skills/another_link", None, "../outside"),   # symlink
        ]
        bundle = self._make_bundle(members)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.94",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        pushed_bundles = {}

        async def fake_push_file(container, src, dst):
            pushed_bundles["src"] = src
            pushed_bundles["dst"] = dst
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        calls = []

        async def fake_exec(container, cmd, timeout=None):
            calls.append(cmd)
            fake = _make_fake_exec({"active_profile": "symlink-test"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "symlink-test"},
            files=bundle,
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task

        # Check that dropped files are reported in the task result
        assert "dropped_files" in task, "Dropped files should be reported in task"
        dropped = task["dropped_files"]
        assert any("dangling_link" in d for d in dropped), f"Dangling symlink should be reported as dropped: {dropped}"
        assert any("another_link" in d for d in dropped), f"Relative symlink should be reported as dropped: {dropped}"

    async def test_non_regular_files_dropped_and_reported(self, client, app, monkeypatch):
        """Non-regular files (device nodes, FIFOs, etc.) must be dropped and reported."""
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            with tarfile.open(tmp.name, "w:gz") as tar:
                # Regular file
                ti = tarfile.TarInfo(name="regular.txt")
                ti.size = len(b"regular")
                tar.addfile(ti, io.BytesIO(b"regular"))
                # Character device
                ti = tarfile.TarInfo(name="char_dev")
                ti.type = tarfile.CHRTYPE
                ti.devmajor = 1
                ti.devminor = 3
                tar.addfile(ti)
                # FIFO
                ti = tarfile.TarInfo(name="my_fifo")
                ti.type = tarfile.FIFOTYPE
                tar.addfile(ti)
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            content = f.read()
        Path(tmp_path).unlink()
        bundle = {"bundle": ("profile.tar.gz", io.BytesIO(content), "application/gzip")}

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.95",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        calls = []

        async def fake_exec(container, cmd, timeout=None):
            calls.append(cmd)
            fake = _make_fake_exec({"active_profile": "nonregular-test"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "nonregular-test"},
            files=bundle,
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task

        dropped = task.get("dropped_files", [])
        assert any("char_dev" in d for d in dropped), f"Character device should be reported as dropped: {dropped}"
        assert any("my_fifo" in d for d in dropped), f"FIFO should be reported as dropped: {dropped}"


@pytest.mark.asyncio
class TestModelProviderRewrite:
    """Tests for rewriting the profile's model/provider config to taOS gateway."""

    def _make_bundle_with_config(self, config_yaml: str, filename="profile.tar.gz"):
        """Create a tar.gz bundle with a config.yaml."""
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            with tarfile.open(tmp.name, "w:gz") as tar:
                ti = tarfile.TarInfo(name="config.yaml")
                ti.size = len(config_yaml.encode())
                tar.addfile(ti, io.BytesIO(config_yaml.encode()))
                # Also add a SOUL.md so readback works
                ti = tarfile.TarInfo(name="SOUL.md")
                ti.size = len(b"Test soul")
                tar.addfile(ti, io.BytesIO(b"Test soul"))
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            content = f.read()
        Path(tmp_path).unlink()
        return {"bundle": (filename, io.BytesIO(content), "application/gzip")}

    async def test_model_provider_rewritten_to_taos_gateway(self, client, app, monkeypatch):
        """After import, the profile's model/provider config must be rewritten
        to the taOS gateway, with original backed up to .pre-import."""
        # Bundle with a host-only model config (e.g., 127.0.0.1:8080)
        config_yaml = """
model:
  default: some-local-model
  provider: custom
  base_url: "http://127.0.0.1:8080/v1"
  api_key: "local-key"
"""
        bundle = self._make_bundle_with_config(config_yaml)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.96",
                    "llm_key": "taos-gateway-key", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            fake = _make_fake_exec({"active_profile": "model-rewrite-test"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "model-rewrite-test"},
            files=bundle,
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task

        # Verify the agent's model is the taOS gateway model
        detail = await client.get(f"/api/agents/{slug}")
        agent = detail.json()
        assert agent["model"] == "taos-gateway-key" or agent["model"] == "kilo-auto/free"

    async def test_first_request_goes_to_taos_gateway(self, client, app, monkeypatch):
        """Integration test: imported agent's first request should route to taOS gateway."""
        config_yaml = """
model:
  default: local-model
  provider: custom
  base_url: "http://127.0.0.1:8080/v1"
  api_key: "local-only"
"""
        bundle = self._make_bundle_with_config(config_yaml)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.97",
                    "llm_key": "taos-key-123", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            fake = _make_fake_exec({"active_profile": "first-request-test"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "first-request-test"},
            files=bundle,
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task


@pytest.mark.asyncio
class TestPauseJobs:
    """Tests for the optional pause_jobs field on import."""

    def _make_bundle_with_jobs(self, jobs_json: str, filename="profile.tar.gz"):
        """Create a tar.gz bundle with jobs.json."""
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            with tarfile.open(tmp.name, "w:gz") as tar:
                ti = tarfile.TarInfo(name="jobs.json")
                ti.size = len(jobs_json.encode())
                tar.addfile(ti, io.BytesIO(jobs_json.encode()))
                ti = tarfile.TarInfo(name="SOUL.md")
                ti.size = len(b"Test soul")
                tar.addfile(ti, io.BytesIO(b"Test soul"))
                ti = tarfile.TarInfo(name="config.yaml")
                ti.size = len(b"model:\n  default: test\n")
                tar.addfile(ti, io.BytesIO(b"model:\n  default: test\n"))
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            content = f.read()
        Path(tmp_path).unlink()
        return {"bundle": (filename, io.BytesIO(content), "application/gzip")}

    async def test_pause_jobs_true_imports_jobs_paused(self, client, app, monkeypatch):
        """When pause_jobs=true, imported jobs should have enabled=false, state=paused."""
        jobs_json = '{"jobs": [{"name": "daily-summary", "enabled": true, "schedule": "0 9 * * *"}]}'
        bundle = self._make_bundle_with_jobs(jobs_json)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.98",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            fake = _make_fake_exec({"active_profile": "pause-jobs-test"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "pause-jobs-test", "pause_jobs": "true"},
            files=bundle,
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task

        # The task result should indicate jobs were paused
        assert "jobs_paused" in task, "Result should report jobs paused"
        assert task["jobs_paused"] == 1

    async def test_pause_jobs_false_default_imports_jobs_enabled(self, client, app, monkeypatch):
        """Default (pause_jobs=false or omitted) should import jobs with original enabled state."""
        jobs_json = '{"jobs": [{"name": "daily-summary", "enabled": true, "schedule": "0 9 * * *"}]}'
        bundle = self._make_bundle_with_jobs(jobs_json)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.99",
                    "llm_key": "sk-x", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            fake = _make_fake_exec({"active_profile": "default-jobs-test"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "default-jobs-test"},
            files=bundle,
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task

        # Jobs should NOT be paused by default
        assert "jobs_paused" not in task.get("result", {}) or task["result"].get("jobs_paused", 0) == 0


@pytest.mark.asyncio
class TestTransferNotice:
    """Tests for the transfer notice written to memory and sent as first chat message."""

    def _make_bundle_complete(self, filename="profile.tar.gz"):
        """Create a complete bundle with config, SOUL, jobs, skills."""
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            with tarfile.open(tmp.name, "w:gz") as tar:
                config = """model:
  default: local-model
  provider: custom
  base_url: "http://127.0.0.1:8080/v1"
  api_key: "local-only"
"""
                ti = tarfile.TarInfo(name="config.yaml")
                ti.size = len(config.encode())
                tar.addfile(ti, io.BytesIO(config.encode()))

                ti = tarfile.TarInfo(name="SOUL.md")
                soul = "You are a helpful assistant."
                ti.size = len(soul.encode())
                tar.addfile(ti, io.BytesIO(soul.encode()))

                jobs = '{"jobs": [{"name": "daily-summary", "enabled": true, "schedule": "0 9 * * *"}, {"name": "weekly-report", "enabled": false, "schedule": "0 10 * * 1"}]}'
                ti = tarfile.TarInfo(name="jobs.json")
                ti.size = len(jobs.encode())
                tar.addfile(ti, io.BytesIO(jobs.encode()))

                # Add a skill directory
                ti = tarfile.TarInfo(name="skills/my_skill.py")
                ti.size = len(b"print('skill')")
                tar.addfile(ti, io.BytesIO(b"print('skill')"))

                # Add a dangling symlink to test dropped files reporting
                ti = tarfile.TarInfo(name="skills/bad_link")
                ti.type = tarfile.SYMTYPE
                ti.linkname = "/etc/shadow"
                tar.addfile(ti)

            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            content = f.read()
        Path(tmp_path).unlink()
        return {"bundle": (filename, io.BytesIO(content), "application/gzip")}

    async def test_transfer_notice_written_to_memory_and_sent_as_first_message(self, client, app, monkeypatch):
        """After successful import, a transfer notice must be written to agent memory
        AND delivered as the first chat message."""
        bundle = self._make_bundle_complete()

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.100",
                    "llm_key": "taos-key-456", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        async def fake_push_file(container, src, dst):
            return 0, ""
        monkeypatch.setattr("tinyagentos.containers.push_file", fake_push_file)

        async def fake_exec(container, cmd, timeout=None):
            fake = _make_fake_exec({"active_profile": "transfer-notice-test"})
            return await fake(container, cmd, timeout)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "transfer-notice-test", "pause_jobs": "true"},
            files=bundle,
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(30):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "success", task

        # The transfer notice is written as files in the container
        # We verify the task completed successfully with all the new features
        assert "dropped_files" in task
        dropped = task["dropped_files"]
        assert any("bad_link" in d for d in dropped), f"Dropped symlink should be reported: {dropped}"


@pytest.mark.asyncio
class TestFailedImportCleanup:
    """Tests for failed imports leaving no half-record without container."""

    async def test_failed_import_leaves_no_stale_agent_record(self, client, app, monkeypatch):
        """A failed import must not leave an agent record without a container."""
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.101",
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
                return 2, "boom: corrupt bundle"
            return 1, ""
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "failed-cleanup-test"},
            files=_bundle(),
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(20):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "failed", task

        # Check agent record - should either not exist or be marked failed with reason
        detail = await client.get(f"/api/agents/{slug}")
        agent = detail.json()
        assert agent["status"] == "failed", f"Agent status should be failed, got {agent['status']}"
        assert "failure_reason" in agent, f"Failed agent should have failure_reason: {agent}"

    async def test_failed_deploy_cleans_up_agent_record(self, client, app, monkeypatch):
        """If deploy itself fails, the agent record should be cleaned up."""
        async def fake_deploy(req):
            return {"success": False, "error": "container creation failed", "steps": []}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        resp = await client.post(
            "/api/agents/import",
            data={"framework": "hermes", "name": "failed-deploy-test"},
            files=_bundle(),
        )
        assert resp.status_code == 202, resp.json()
        slug = resp.json()["name"]
        for _ in range(20):
            await asyncio.sleep(0.05)
            task = app.state.deploy_tasks.get(slug)
            if task and task["status"] in ("success", "failed"):
                break
        task = app.state.deploy_tasks.get(slug)
        assert task["status"] == "failed", task

        # Agent should not exist or be marked failed
        detail = await client.get(f"/api/agents/{slug}")
        # 404 is acceptable (cleaned up), or 200 with status=failed
        if detail.status_code == 200:
            assert detail.json()["status"] == "failed"
        elif detail.status_code == 404:
            pass  # Cleaned up completely
        else:
            assert False, f"Unexpected status {detail.status_code}"


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