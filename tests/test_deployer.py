from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.deployer import deploy_agent, undeploy_agent, DeployRequest, _splice_taosmd_block, AGENTS_MD_PATHS


def _req(**overrides) -> DeployRequest:
    defaults = dict(
        name="test",
        framework="smolagents",
        model=None,
        data_dir=Path("/tmp/taos-test-data"),
    )
    defaults.update(overrides)
    return DeployRequest(**defaults)


class TestDeployAgent:
    @pytest.mark.asyncio
    async def test_full_deployment_flow(self, tmp_path):
        req = _req(data_dir=tmp_path)

        async def mock_exec(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-test"}

            result = await deploy_agent(req)
            assert result["success"] is True
            assert result["name"] == "test"
            assert result["container"] == "taos-agent-test"
            assert result["ip"] == "10.0.0.5"
            assert "deployment_complete" in result["steps"]

            call_kwargs = mock_create.call_args.kwargs
            env = call_kwargs["env"]
            assert env["TAOS_AGENT_NAME"] == "test"
            assert env["TAOS_SKILLS_URL"].endswith("/api/skill-exec")

    @pytest.mark.asyncio
    async def test_one_trace_bind_mount(self, tmp_path):
        """After deploy, create_container receives exactly one mount: the trace dir."""
        req = _req(data_dir=tmp_path)

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-test"}
            result = await deploy_agent(req)
            assert result["success"] is True

            mounts = mock_create.call_args.kwargs["mounts"]
            assert len(mounts) == 1
            host_path, container_path = mounts[0]
            assert container_path == "/root/.taos/trace"
            assert str(tmp_path / "trace" / "test") == host_path

    @pytest.mark.asyncio
    async def test_no_workspace_memory_home_mount(self, tmp_path):
        """The three old bind mounts must NOT appear in the mounts list."""
        req = _req(data_dir=tmp_path)

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-test"}
            await deploy_agent(req)

            mounts = mock_create.call_args.kwargs["mounts"]
            container_paths = [m[1] for m in mounts]
            assert "/workspace" not in container_paths
            assert "/memory" not in container_paths
            assert "/root" not in container_paths

    @pytest.mark.asyncio
    async def test_root_quota_passed_through_default(self, tmp_path):
        """create_container receives root_size_gib=40 by default."""
        req = _req(data_dir=tmp_path)

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-test"}
            await deploy_agent(req)
            assert mock_create.call_args.kwargs["root_size_gib"] == 40

    @pytest.mark.asyncio
    async def test_root_quota_custom_value_honoured(self, tmp_path):
        """Custom root_size_gib on DeployRequest reaches create_container."""
        req = _req(data_dir=tmp_path, root_size_gib=80)

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-test"}
            await deploy_agent(req)
            assert mock_create.call_args.kwargs["root_size_gib"] == 80

    @pytest.mark.asyncio
    async def test_trace_dir_created_on_host(self, tmp_path):
        """The trace host dir is created before container creation."""
        req = _req(data_dir=tmp_path)

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-test"}
            await deploy_agent(req)
            assert (tmp_path / "trace" / "test").is_dir()

    @pytest.mark.asyncio
    async def test_handles_container_creation_failure(self, tmp_path):
        req = _req(name="fail", data_dir=tmp_path)
        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {"success": False, "error": "no space"}
            result = await deploy_agent(req)
            assert result["success"] is False
            assert "no space" in result["error"]

    @pytest.mark.asyncio
    async def test_deployment_with_llm_proxy_injects_embedding_url(self, tmp_path):
        """LLM proxy wired - OPENAI_BASE_URL and TAOS_EMBEDDING_URL land in env."""
        mock_proxy = MagicMock()
        mock_proxy.is_running.return_value = True
        mock_proxy.url = "http://localhost:4000"
        mock_proxy.database_url = None
        mock_proxy.create_agent_key = AsyncMock(return_value="sk-test-key-123")

        req = _req(
            name="proxy-test",
            data_dir=tmp_path,
            extra_config={"llm_proxy": mock_proxy},
        )

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.6")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-proxy-test"}

            result = await deploy_agent(req)
            assert result["success"] is True
            assert result["llm_key"] == "sk-test-key-123"
            env = mock_create.call_args.kwargs["env"]
            assert env["OPENAI_API_KEY"] == "sk-test-key-123"
            assert env["OPENAI_BASE_URL"] == "http://localhost:4000/v1"
            assert env["TAOS_EMBEDDING_URL"] == "http://localhost:4000/v1/embeddings"
            # No model was specified on this DeployRequest, so the
            # deployer passes models=None and create_agent_key falls
            # back internally to its "default" alias.
            mock_proxy.create_agent_key.assert_called_once_with(
                "proxy-test", models=None
            )

    @pytest.mark.asyncio
    async def test_fallback_to_master_key_when_create_returns_none(self, tmp_path):
        """When LiteLLM runs without a Postgres DB, create_agent_key
        returns None. The deployer must fall back to the shared master
        key so the container still gets a non-empty LITELLM_API_KEY —
        otherwise openclaw's gateway crashes on boot."""
        mock_proxy = MagicMock()
        mock_proxy.is_running.return_value = True
        mock_proxy.url = "http://localhost:4000"
        mock_proxy.database_url = None
        mock_proxy.create_agent_key = AsyncMock(return_value=None)

        req = _req(
            name="routing-only",
            data_dir=tmp_path,
            extra_config={"llm_proxy": mock_proxy},
        )

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.14")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-routing-only"}
            result = await deploy_agent(req)
            assert result["success"] is True
            env = mock_create.call_args.kwargs["env"]
            assert env["LITELLM_API_KEY"] == "sk-taos-master"
            assert env["OPENAI_API_KEY"] == "sk-taos-master"
            assert env["OPENAI_BASE_URL"] == "http://localhost:4000/v1"
            # Embedding env must still fire under the fallback path — the
            # LiteLLM routing-only mode serves /v1/embeddings identically.
            assert env["TAOS_EMBEDDING_URL"] == "http://localhost:4000/v1/embeddings"
            assert env["TAOS_EMBEDDING_MODEL"] == "taos-embedding-default"

    @pytest.mark.asyncio
    async def test_create_agent_key_called_with_agent_models(self, tmp_path):
        """The virtual key's model scope must match what the agent is
        allowed to call — primary + fallbacks — so LiteLLM rejects any
        off-scope request instead of silently routing it via the master
        key's unrestricted scope."""
        mock_proxy = MagicMock()
        mock_proxy.is_running.return_value = True
        mock_proxy.url = "http://localhost:4000"
        mock_proxy.database_url = "postgresql://u:p@h/db"
        mock_proxy.create_agent_key = AsyncMock(return_value="sk-scoped-key")

        req = _req(
            name="scoped",
            model="kilo-auto/free",
            fallback_models=["kilo-auto/balanced", "kilo-auto/frontier"],
            data_dir=tmp_path,
            extra_config={"llm_proxy": mock_proxy},
        )

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.77")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-scoped"}
            result = await deploy_agent(req)
            assert result["success"] is True
            mock_proxy.create_agent_key.assert_called_once_with(
                "scoped",
                models=["kilo-auto/free", "kilo-auto/balanced", "kilo-auto/frontier"],
            )

    @pytest.mark.asyncio
    async def test_deploy_fails_loudly_when_db_configured_but_key_mint_fails(self, tmp_path):
        """DB configured + /key/generate returns None → deploy fails with a
        clear error. Falling back to the master key here would hide real
        LiteLLM bugs (migration pending, DB unreachable, master key drift)
        and ship broken agents."""
        mock_proxy = MagicMock()
        mock_proxy.is_running.return_value = True
        mock_proxy.url = "http://localhost:4000"
        mock_proxy.database_url = "postgresql://litellm:secret@127.0.0.1:5432/litellm"
        mock_proxy.create_agent_key = AsyncMock(return_value=None)

        req = _req(
            name="db-broken",
            data_dir=tmp_path,
            extra_config={"llm_proxy": mock_proxy},
        )

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.15")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-db-broken"}
            result = await deploy_agent(req)
            assert result["success"] is False
            assert "virtual key mint failed" in result["error"]
            # Host part of the DB URL is mentioned so operators can see
            # which DB instance is misbehaving without leaking credentials.
            assert "127.0.0.1:5432/litellm" in result["error"]
            assert "secret" not in result["error"]

    @pytest.mark.asyncio
    async def test_deployment_without_llm_proxy(self, tmp_path):
        req = _req(name="no-proxy", data_dir=tmp_path)

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.7")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-no-proxy"}

            result = await deploy_agent(req)
            assert result["success"] is True
            assert result.get("llm_key") is None
            env = mock_create.call_args.kwargs["env"]
            assert "TAOS_EMBEDDING_URL" not in env

    @pytest.mark.asyncio
    async def test_model_name_injected_into_env(self, tmp_path):
        req = _req(name="with-model", model="claude-3.5-sonnet", data_dir=tmp_path)

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.8")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-with-model"}
            result = await deploy_agent(req)
            assert result["success"] is True
            env = mock_create.call_args.kwargs["env"]
            assert env["TAOS_MODEL"] == "claude-3.5-sonnet"

    @pytest.mark.asyncio
    async def test_base_deps_include_npm_and_build_essentials(self, tmp_path):
        req = _req(name="deps", data_dir=tmp_path)
        recorded = []

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            recorded.append(cmd_str)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.9")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-deps"}
            await deploy_agent(req)

        apt_commands = [c for c in recorded if "apt-get install" in c]
        assert apt_commands, "expected an apt-get install command"
        apt = apt_commands[0]
        for pkg in ("nodejs", "npm", "build-essential", "ca-certificates", "python3"):
            assert pkg in apt, f"{pkg} missing from apt install: {apt}"

    @pytest.mark.asyncio
    async def test_framework_install_failure_rolls_back(self, tmp_path):
        req = _req(name="brokenfw", framework="nonexistent-pkg", data_dir=tmp_path)

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.10")
            # Match the pip path (with --break-system-packages flag) and the
            # script install path so the rollback fires regardless of which
            # branch the deployer takes for this framework slug.
            if ("pip3 install" in cmd_str and "nonexistent-pkg" in cmd_str) or \
                    cmd_str.startswith("bash /tmp/install_"):
                return (1, "ERROR: No matching distribution found")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.destroy_container", new_callable=AsyncMock) as mock_destroy:
            mock_create.return_value = {"success": True, "name": "taos-agent-brokenfw"}
            mock_destroy.return_value = {"success": True}
            result = await deploy_agent(req)
            assert result["success"] is False
            assert "Framework install failed" in result["error"]
            mock_destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_manifest_pip_install(self, tmp_path):
        # Use a framework slug that has no in-repo install_<name>.sh so the
        # deployer exercises the manifest pip path (not the script-first path).
        mock_manifest = MagicMock()
        mock_manifest.install = {"method": "pip", "package": "crewai"}
        mock_manifest.manifest_dir = tmp_path
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_manifest

        req = _req(
            name="pip-install", framework="crewai", data_dir=tmp_path,
            extra_config={"registry": mock_registry},
        )

        recorded = []
        async def mock_exec_fn(name, cmd, **kwargs):
            recorded.append(" ".join(cmd))
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.11")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-pip-install"}
            result = await deploy_agent(req)
            assert result["success"] is True
            assert any("pip3 install" in c and "crewai" in c for c in recorded)
            mock_registry.get.assert_called_once_with("crewai")

    @pytest.mark.asyncio
    async def test_manifest_script_install(self, tmp_path):
        script_dir = tmp_path / "openclaw"
        script_dir.mkdir()
        script_path = script_dir / "install.sh"
        script_path.write_text("#!/bin/bash\necho installing\n")

        mock_manifest = MagicMock()
        mock_manifest.install = {"method": "script", "script": "install.sh"}
        mock_manifest.manifest_dir = script_dir
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_manifest

        req = _req(
            name="scripted", framework="openclaw", data_dir=tmp_path,
            extra_config={"registry": mock_registry},
        )

        recorded_execs = []
        async def mock_exec_fn(name, cmd, **kwargs):
            recorded_execs.append(" ".join(cmd))
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.12")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock) as mock_push:
            mock_create.return_value = {"success": True, "name": "taos-agent-scripted"}
            mock_push.return_value = (0, "")
            result = await deploy_agent(req)
            assert result["success"] is True
            # At least one push_file call must be for the install script
            install_pushes = [
                c for c in mock_push.call_args_list
                if c.args[2] == "/tmp/install.sh"
            ]
            assert install_pushes, "install.sh was not pushed"
            assert any("bash /tmp/install.sh" in c for c in recorded_execs)

    @pytest.mark.asyncio
    async def test_agent_home_env_set(self, tmp_path):
        """TAOS_AGENT_HOME is always /root in the snapshot model."""
        req = _req(name="homer", data_dir=tmp_path)

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.80")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-homer"}
            result = await deploy_agent(req)
            assert result["success"] is True
            env = mock_create.call_args.kwargs["env"]
            assert env["TAOS_AGENT_HOME"] == "/root"

    @pytest.mark.asyncio
    async def test_manifest_script_missing_file_fails(self, tmp_path):
        mock_manifest = MagicMock()
        mock_manifest.install = {"method": "script", "script": "install.sh"}
        mock_manifest.manifest_dir = tmp_path
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_manifest

        req = _req(
            name="missingscript", framework="openclaw", data_dir=tmp_path,
            extra_config={"registry": mock_registry},
        )

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.13")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.destroy_container", new_callable=AsyncMock) as mock_destroy:
            mock_create.return_value = {"success": True, "name": "taos-agent-missingscript"}
            mock_destroy.return_value = {"success": True}
            result = await deploy_agent(req)
            assert result["success"] is False
            assert "Install script missing" in result["error"]

    @pytest.mark.asyncio
    async def test_proxy_devices_attached(self, tmp_path):
        req = _req(name="proxied", data_dir=tmp_path)

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.90")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock) as mock_proxy:
            mock_create.return_value = {"success": True, "name": "taos-agent-proxied"}
            mock_proxy.return_value = {"success": True, "output": ""}
            result = await deploy_agent(req)
            assert result["success"] is True

            calls = mock_proxy.call_args_list
            dev_names = {c.args[1] for c in calls}
            listens = {c.kwargs["listen"] for c in calls}
            assert "taos-proxy-litellm" in dev_names
            assert "taos-proxy-taos" in dev_names
            assert "tcp:127.0.0.1:4000" in listens
            assert "tcp:127.0.0.1:6969" in listens

    @pytest.mark.asyncio
    async def test_proxy_device_failure_rolls_back(self, tmp_path):
        req = _req(name="noproxy", data_dir=tmp_path)

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.91")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock) as mock_proxy, \
             patch("tinyagentos.deployer.destroy_container", new_callable=AsyncMock) as mock_destroy:
            mock_create.return_value = {"success": True, "name": "taos-agent-noproxy"}
            mock_proxy.return_value = {"success": False, "output": "device already exists"}
            mock_destroy.return_value = {"success": True}
            result = await deploy_agent(req)
            assert result["success"] is False
            assert "proxy device" in result["error"]
            mock_destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_uses_base_image_when_present(self, tmp_path):
        """When taos-openclaw-base is imported, the deployer launches from
        that alias (not images:debian/bookworm) and sets TAOS_BASE_IMAGE_PRESENT=1."""
        req = _req(name="cached", framework="openclaw", data_dir=tmp_path)
        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.50")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.is_image_present", new_callable=AsyncMock, return_value=True):
            mock_create.return_value = {"success": True, "name": "taos-agent-cached"}
            result = await deploy_agent(req)
            assert result["success"] is True
            assert mock_create.call_args.kwargs["image"] == "taos-openclaw-base"
            env = mock_create.call_args.kwargs["env"]
            assert env["TAOS_BASE_IMAGE_PRESENT"] == "1"
            assert "deps_skipped_base_image" in result["steps"]

    @pytest.mark.asyncio
    async def test_deploy_falls_back_when_base_image_absent(self, tmp_path):
        """Missing cache image = legacy path: images:debian/bookworm + apt-get install."""
        req = _req(name="cold", framework="openclaw", data_dir=tmp_path)
        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.51")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.is_image_present", new_callable=AsyncMock, return_value=False):
            mock_create.return_value = {"success": True, "name": "taos-agent-cold"}
            result = await deploy_agent(req)
            assert result["success"] is True
            assert mock_create.call_args.kwargs["image"] == "images:debian/bookworm"
            env = mock_create.call_args.kwargs["env"]
            assert "TAOS_BASE_IMAGE_PRESENT" not in env
            assert "deps_installed" in result["steps"]

    @pytest.mark.asyncio
    async def test_openclaw_deploy_pushes_agents_md_with_taosmd_rules(self, tmp_path):
        """When framework=openclaw and no existing AGENTS.md, deploy pushes a
        sentinel-wrapped file with taosmd agent_rules and the agent name substituted."""
        pushed: list[tuple[str, str]] = []

        async def fake_push_file(container, src, dst):
            try:
                with open(src) as fh:
                    pushed.append((dst, fh.read()))
            except FileNotFoundError:
                pushed.append((dst, ""))
            return 0, ""

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.99")
            if "cat" in cmd_str and "AGENTS.md" in cmd_str:
                # Simulate no existing file
                return (1, "")
            return (0, "ok")

        fake_rules = "Follow the taosmd librarian protocol.\nAgent: <your-agent-name>"

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", side_effect=fake_push_file), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.is_image_present", new_callable=AsyncMock, return_value=False):
            mock_create.return_value = {"success": True, "name": "taos-agent-octest"}

            import types
            fake_taosmd = types.ModuleType("taosmd")
            fake_taosmd.agent_rules = lambda: fake_rules

            import sys
            sys.modules["taosmd"] = fake_taosmd
            try:
                req = _req(name="octest", framework="openclaw", data_dir=tmp_path)
                result = await deploy_agent(req)
            finally:
                sys.modules.pop("taosmd", None)

        assert result["success"] is True
        agents_md_entries = [(dst, content) for dst, content in pushed if dst == "/root/.openclaw/AGENTS.md"]
        assert agents_md_entries, "AGENTS.md was not pushed to /root/.openclaw/AGENTS.md"
        _dst, content = agents_md_entries[0]
        assert "octest" in content, "agent slug not substituted in AGENTS.md"
        assert "librarian" in content, "expected taosmd rules phrase in AGENTS.md"
        assert "<your-agent-name>" not in content, "placeholder not replaced"
        assert "<!-- taosmd:rules-begin -->" in content, "missing begin sentinel"
        assert "<!-- taosmd:rules-end -->" in content, "missing end sentinel"

    @pytest.mark.asyncio
    async def test_hermes_deploy_pushes_agents_md_with_taosmd_rules(self, tmp_path):
        """When framework=hermes and no existing AGENTS.md, deploy pushes a
        sentinel-wrapped file to /root/.hermes/AGENTS.md with taosmd agent_rules
        and the agent name substituted."""
        pushed: list[tuple[str, str]] = []

        async def fake_push_file(container, src, dst):
            try:
                with open(src) as fh:
                    pushed.append((dst, fh.read()))
            except FileNotFoundError:
                pushed.append((dst, ""))
            return 0, ""

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.98")
            if "cat" in cmd_str and "AGENTS.md" in cmd_str:
                return (1, "")
            return (0, "ok")

        fake_rules = "Hermes agent protocol.\nAgent: <your-agent-name>"

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", side_effect=fake_push_file), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.is_image_present", new_callable=AsyncMock, return_value=False):
            mock_create.return_value = {"success": True, "name": "taos-agent-hermestest"}

            import types
            fake_taosmd = types.ModuleType("taosmd")
            fake_taosmd.agent_rules = lambda: fake_rules

            import sys
            sys.modules["taosmd"] = fake_taosmd
            try:
                req = _req(name="hermestest", framework="hermes", data_dir=tmp_path)
                result = await deploy_agent(req)
            finally:
                sys.modules.pop("taosmd", None)

        assert result["success"] is True
        agents_md_entries = [(dst, content) for dst, content in pushed if dst == "/root/.hermes/AGENTS.md"]
        assert agents_md_entries, "AGENTS.md was not pushed to /root/.hermes/AGENTS.md"
        _dst, content = agents_md_entries[0]
        assert "hermestest" in content, "agent slug not substituted in AGENTS.md"
        assert "Hermes agent protocol" in content, "expected taosmd rules phrase in AGENTS.md"
        assert "<your-agent-name>" not in content, "placeholder not replaced"
        assert "<!-- taosmd:rules-begin -->" in content, "missing begin sentinel"
        assert "<!-- taosmd:rules-end -->" in content, "missing end sentinel"

    @pytest.mark.asyncio
    async def test_unknown_framework_does_not_push_agents_md(self, tmp_path):
        """A framework not in AGENTS_MD_PATHS must not trigger AGENTS.md injection."""
        pushed_dsts: list[str] = []

        async def fake_push_file(container, src, dst):
            pushed_dsts.append(dst)
            return 0, ""

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.97")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", side_effect=fake_push_file), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-unknown-fw"}
            req = _req(name="unknown-fw", framework="hermes_typo", data_dir=tmp_path)
            result = await deploy_agent(req)

        assert result["success"] is True
        assert "hermes_typo" not in AGENTS_MD_PATHS
        agents_md_pushes = [d for d in pushed_dsts if "AGENTS.md" in d]
        assert not agents_md_pushes, f"AGENTS.md should not be pushed for unknown framework, got: {agents_md_pushes}"

    @pytest.mark.asyncio
    async def test_bridge_url_injected_into_env(self, tmp_path):
        """TAOS_BRIDGE_URL is injected so install.sh can write the openclaw env."""
        req = _req(name="bridge-test", data_dir=tmp_path, taos_port=6969)

        async def mock_exec_fn(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-bridge-test"}
            await deploy_agent(req)
            env = mock_create.call_args.kwargs["env"]
            assert env["TAOS_BRIDGE_URL"] == "http://127.0.0.1:6969"


class TestSpliceTaosmdBlock:
    """Unit tests for the _splice_taosmd_block helper — no I/O involved."""

    BEGIN = "<!-- taosmd:rules-begin -->"
    END = "<!-- taosmd:rules-end -->"

    def test_no_existing_file_produces_sentinel_only(self):
        result = _splice_taosmd_block("", "my rules")
        assert result == f"{self.BEGIN}\nmy rules\n{self.END}\n"

    def test_existing_with_sentinels_replaces_block_preserves_surroundings(self):
        existing = (
            "# My Agent\n\nSome user content.\n\n"
            f"{self.BEGIN}\nold rules\n{self.END}\n\n"
            "User content below.\n"
        )
        result = _splice_taosmd_block(existing, "new rules")
        assert "Some user content." in result
        assert "User content below." in result
        assert "old rules" not in result
        assert "new rules" in result
        assert result.count(self.BEGIN) == 1
        assert result.count(self.END) == 1

    def test_existing_without_sentinels_appends_block(self):
        existing = "# My Agent\n\nHand-crafted user content.\n"
        result = _splice_taosmd_block(existing, "appended rules")
        assert result.startswith("# My Agent")
        assert "Hand-crafted user content." in result
        assert "appended rules" in result
        assert self.BEGIN in result
        assert self.END in result
        # User content comes before the sentinels
        user_pos = result.index("Hand-crafted")
        sentinel_pos = result.index(self.BEGIN)
        assert user_pos < sentinel_pos

    def test_redeploy_updates_only_block_content(self):
        rules_v1 = "rules version 1"
        rules_v2 = "rules version 2"
        user_header = "# My Agent\n\nUser stuff.\n"
        user_footer = "\nUser footer.\n"
        after_first = _splice_taosmd_block("", rules_v1)
        # Simulate user editing the file around the block
        with_user = user_header + after_first + user_footer
        result = _splice_taosmd_block(with_user, rules_v2)
        assert "User stuff." in result
        assert "User footer." in result
        assert rules_v1 not in result
        assert rules_v2 in result
        assert result.count(self.BEGIN) == 1


class TestOpenClawSpliceDeploy:
    """Integration-level tests for sentinel-aware AGENTS.md push via deploy_agent."""

    @pytest.mark.asyncio
    async def test_existing_file_with_sentinels_preserves_user_content(self, tmp_path):
        """When the container already has AGENTS.md with sentinels, user content
        outside the block is kept and only the taosmd block is updated."""
        import types, sys

        pushed: list[tuple[str, str]] = []
        existing_file = (
            "# Agent rules\n\nMy custom rules.\n\n"
            "<!-- taosmd:rules-begin -->\nstale rules\n<!-- taosmd:rules-end -->\n\n"
            "Footer content.\n"
        )

        async def fake_push_file(container, src, dst):
            with open(src) as fh:
                pushed.append((dst, fh.read()))
            return 0, ""

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.100")
            if "cat" in cmd_str and "AGENTS.md" in cmd_str:
                return (0, existing_file)
            return (0, "ok")

        fake_taosmd = types.ModuleType("taosmd")
        fake_taosmd.agent_rules = lambda: "fresh rules for <your-agent-name>"

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", side_effect=fake_push_file), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.is_image_present", new_callable=AsyncMock, return_value=False):
            mock_create.return_value = {"success": True, "name": "taos-agent-splice-test"}
            sys.modules["taosmd"] = fake_taosmd
            try:
                req = _req(name="splice-test", framework="openclaw", data_dir=tmp_path)
                result = await deploy_agent(req)
            finally:
                sys.modules.pop("taosmd", None)

        assert result["success"] is True
        agents_pushed = [(dst, c) for dst, c in pushed if dst == "/root/.openclaw/AGENTS.md"]
        assert agents_pushed
        content = agents_pushed[0][1]
        assert "My custom rules." in content
        assert "Footer content." in content
        assert "stale rules" not in content
        assert "fresh rules for splice-test" in content

    @pytest.mark.asyncio
    async def test_existing_file_without_sentinels_preserves_and_appends(self, tmp_path):
        """When the container AGENTS.md has no sentinels (user template), we
        append the block at the end without touching user content."""
        import types, sys

        pushed: list[tuple[str, str]] = []
        existing_file = "# My handcrafted template\n\nDo not overwrite me.\n"

        async def fake_push_file(container, src, dst):
            with open(src) as fh:
                pushed.append((dst, fh.read()))
            return 0, ""

        async def mock_exec_fn(name, cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "hostname -I" in cmd_str:
                return (0, "10.0.0.101")
            if "cat" in cmd_str and "AGENTS.md" in cmd_str:
                return (0, existing_file)
            return (0, "ok")

        fake_taosmd = types.ModuleType("taosmd")
        fake_taosmd.agent_rules = lambda: "appended rules <your-agent-name>"

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec_fn), \
             patch("tinyagentos.deployer.push_file", side_effect=fake_push_file), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.deployer.is_image_present", new_callable=AsyncMock, return_value=False):
            mock_create.return_value = {"success": True, "name": "taos-agent-append-test"}
            sys.modules["taosmd"] = fake_taosmd
            try:
                req = _req(name="append-test", framework="openclaw", data_dir=tmp_path)
                result = await deploy_agent(req)
            finally:
                sys.modules.pop("taosmd", None)

        assert result["success"] is True
        agents_pushed = [(dst, c) for dst, c in pushed if dst == "/root/.openclaw/AGENTS.md"]
        assert agents_pushed
        content = agents_pushed[0][1]
        assert "Do not overwrite me." in content
        assert "appended rules append-test" in content
        assert content.index("Do not overwrite me.") < content.index("<!-- taosmd:rules-begin -->")


class TestBackgroundDeploy:
    @pytest.mark.asyncio
    async def test_deploy_endpoint_returns_immediately(self, client):
        resp = await client.post("/api/agents/deploy", json={
            "name": "bg-test",
            "framework": "none",
            "color": "#aabbcc",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deploying"
        assert data["name"] == "bg-test"

    @pytest.mark.asyncio
    async def test_deploy_status_endpoint(self, client):
        await client.post("/api/agents/deploy", json={
            "name": "status-test",
            "framework": "none",
        })
        resp = await client.get("/api/agents/status-test/deploy-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("deploying", "success", "failed")

    @pytest.mark.asyncio
    async def test_deploy_status_not_found(self, client):
        resp = await client.get("/api/agents/nonexistent/deploy-status")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deploy_failure_emits_notification(self, client, app):
        """When background deploy fails, a notification must be pushed so the
        tray surfaces the error rather than leaving the user stuck on 'deploying'."""
        import asyncio

        with patch(
            "tinyagentos.deployer.deploy_agent",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "container create failed"},
        ):
            await client.post("/api/agents/deploy", json={
                "name": "notif-fail-test",
                "framework": "none",
            })
            # Yield to the event loop so the background task runs to completion.
            await asyncio.sleep(0.1)

        notifs = await app.state.notifications.list(limit=20)
        error_notifs = [n for n in notifs if "notif-fail-test" in n.get("message", "")]
        assert error_notifs, "Expected a notification for the failed deploy"
        assert error_notifs[0].get("level") == "error"
        assert "container create failed" in error_notifs[0].get("message", "")


class TestUndeployAgent:
    @pytest.mark.asyncio
    async def test_undeploy(self):
        with patch("tinyagentos.deployer.destroy_container", new_callable=AsyncMock) as mock_destroy:
            mock_destroy.return_value = {"success": True, "output": ""}
            result = await undeploy_agent("test")
            assert result["success"] is True
            mock_destroy.assert_called_once_with("taos-agent-test")


class TestUndeployWithStateCleanup:
    @pytest.mark.asyncio
    async def test_undeploy_delete_state_wipes_dirs(self, tmp_path):
        (tmp_path / "agent-workspaces" / "wiper").mkdir(parents=True)
        (tmp_path / "agent-memory" / "wiper").mkdir(parents=True)
        (tmp_path / "agent-workspaces" / "wiper" / "marker.txt").write_text("x")

        with patch("tinyagentos.deployer.destroy_container", new_callable=AsyncMock) as mock_destroy:
            mock_destroy.return_value = {"success": True, "output": ""}
            result = await undeploy_agent("wiper", data_dir=tmp_path, delete_state=True)
            assert result["success"] is True
            assert not (tmp_path / "agent-workspaces" / "wiper").exists()
            assert not (tmp_path / "agent-memory" / "wiper").exists()

    @pytest.mark.asyncio
    async def test_undeploy_without_delete_state_keeps_dirs(self, tmp_path):
        (tmp_path / "agent-workspaces" / "keeper").mkdir(parents=True)
        with patch("tinyagentos.deployer.destroy_container", new_callable=AsyncMock) as mock_destroy:
            mock_destroy.return_value = {"success": True, "output": ""}
            result = await undeploy_agent("keeper", data_dir=tmp_path, delete_state=False)
            assert result["success"] is True
            assert (tmp_path / "agent-workspaces" / "keeper").exists()


class TestContainerFailureExplanation:
    """The opaque incus 'idmapped storage / change ownership' failure (nested
    deploy in an unprivileged LXC) must be translated into actionable guidance.
    See the discussion #357 LXC investigation."""

    def test_idmap_failure_gives_privileged_container_guidance(self):
        from tinyagentos.deployer import _explain_container_failure
        raw = ("Launching taos-agent-x\nError: Failed instance creation: Failed "
               "to handle idmapped storage: Failed to change ownership of: "
               "/var/lib/incus/storage-pools/default/containers/taos-agent-x/rootfs")
        msg = _explain_container_failure(raw)
        assert "privileged" in msg.lower()
        assert "nesting" in msg.lower()
        assert raw in msg  # underlying error preserved for debugging

    def test_generic_failure_passes_through_when_privileged(self, monkeypatch):
        from tinyagentos import deployer
        # Pretend we're NOT in an unprivileged userns so a generic error isn't
        # misattributed to the container-privilege problem.
        monkeypatch.setattr(deployer, "_is_unprivileged_userns", lambda: False)
        msg = deployer._explain_container_failure("no space left on device")
        assert msg == "Container creation failed: no space left on device"
        assert "privileged" not in msg.lower()

    def test_unprivileged_userns_flags_even_generic_error(self, monkeypatch):
        from tinyagentos import deployer
        monkeypatch.setattr(deployer, "_is_unprivileged_userns", lambda: True)
        msg = deployer._explain_container_failure("some other failure")
        assert "privileged" in msg.lower()

    def test_uid_map_parsing(self, tmp_path):
        from tinyagentos import deployer
        # privileged / host: root maps to 0
        f = tmp_path / "privileged"; f.write_text("         0          0 4294967295\n")
        assert deployer._is_unprivileged_userns(str(f)) is False
        # unprivileged: root maps to 100000
        f2 = tmp_path / "unpriv"; f2.write_text("         0     100000      65536\n")
        assert deployer._is_unprivileged_userns(str(f2)) is True
        # missing file (e.g. macOS): not an unprivileged Linux userns
        assert deployer._is_unprivileged_userns(str(tmp_path / "nope")) is False
