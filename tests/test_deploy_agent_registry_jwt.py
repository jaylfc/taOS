"""Tests for deployed agent registry JWT (TAOS_AGENT_TOKEN).

Verifies that POST /api/agents/deploy:
  - passes user_id to agent_registry.register()
  - adds a memory_read grant for the canonical_id
  - mints a registry JWT and passes it as TAOS_AGENT_TOKEN to the container
  - the registry row has correct user_id and origin
  - the grants store lists exactly memory_read for that canonical_id

Also unit-tests the deployer half: DeployRequest(agent_token="x", ...) puts
TAOS_AGENT_TOKEN=x in the env and the empty default leaves it absent.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from tinyagentos.deployer import DeployRequest, deploy_agent
from tinyagentos.agent_registry_store import verify_registry_token


class TestDeployAgentRegistryJWT:
    """Endpoint-level tests for the registry JWT minting in the deploy route."""

    @pytest.mark.asyncio
    async def test_deploy_mints_registry_jwt_and_passes_to_container(self, client, app, tmp_path):
        """POST /api/agents/deploy mints a registry JWT for the deployed agent
        and passes it as TAOS_AGENT_TOKEN to the container via DeployRequest.
        """
        # Mock the deploy_agent function to capture the DeployRequest
        captured_request = {}

        async def mock_deploy_agent(req: DeployRequest) -> dict:
            captured_request["req"] = req
            return {
                "success": True,
                "name": req.name,
                "container": f"taos-agent-{req.name}",
                "ip": "10.0.0.5",
                "llm_key": "sk-test-key",
                "steps": ["deployment_complete"],
                "versioning": True,
                "versioning_error": None,
            }

        with patch("tinyagentos.deployer.deploy_agent", mock_deploy_agent):
            # Mock archive to avoid side effects
            mock_archive = AsyncMock()
            mock_archive.record = AsyncMock()
            mock_archive.query = AsyncMock(return_value=[{"event_type": "agent_deployed"}])
            app.state.archive = mock_archive

            # Mock registry to return a canonical_id
            mock_registry = MagicMock()
            mock_registry._db = True  # so the route thinks it's initialized
            mock_registry.register = AsyncMock(return_value={
                "canonical_id": "test-agent-20261029-120000",
                "display_name": "Test Agent",
                "framework": "none",
                "user_id": "test-user-id",
                "origin": "taos-deployed",
                "handle": "",
                "role": None,
                "capabilities": "[]",
                "created_ts": "2026-10-29T12:00:00+00:00",
                "revoked_at": None,
                "status": "active",
                "sponsor_contact_id": None,
                "install_id": "",
            })
            app.state.agent_registry = mock_registry

            # Mock grants store
            mock_grants = AsyncMock()
            mock_grants.add_grant = AsyncMock()
            mock_grants.list_grants = AsyncMock(return_value=[
                {"canonical_id": "test-agent-20261029-120000", "scope": "memory_read", "tier": "once", "project_id": None, "granted_at": "2026-10-29T12:00:00+00:00", "expires_at": None}
            ])
            app.state.agent_grants = mock_grants

            # Mock the keypair for token minting
            from tinyagentos.agent_registry_store import load_or_create_signing_keypair
            private_pem, public_pem = load_or_create_signing_keypair(tmp_path)
            app.state.agent_registry_keypair = (private_pem, public_pem)

            # Mock other required state
            app.state.llm_proxy = None
            app.state.secrets = None
            app.state.registry = None
            app.state.taosmd_agent_registry = MagicMock()
            app.state.taosmd_agent_registry.register_agent = AsyncMock()
            app.state.notifications = None
            app.state.deploy_tasks = {}
            app.state.idempotency_cache = None
            app.state.cluster_manager = None

            # Ensure config has agents list
            from tinyagentos.config import AppConfig
            app.state.config = AppConfig(config_path=tmp_path / "config.yaml")
            app.state.config.agents = []

            # Make the request
            r = await client.post("/api/agents/deploy", json={"name": "Test Agent", "framework": "none"})

            # Check response
            assert r.status_code == 200, f"Deploy failed: {r.text}"

            # Verify the DeployRequest was captured with agent_token
            assert "req" in captured_request, "deploy_agent was not called"
            req = captured_request["req"]
            assert hasattr(req, "agent_token"), "DeployRequest missing agent_token field"
            assert req.agent_token, "agent_token should be non-empty"

            # Verify the token is valid and has correct sub claim
            payload = verify_registry_token(req.agent_token, public_pem)
            assert payload["sub"] == "test-agent-20261029-120000", f"Token sub mismatch: {payload['sub']}"

            # Verify registry.register was called with user_id
            mock_registry.register.assert_called_once()
            call_kwargs = mock_registry.register.call_args.kwargs
            assert call_kwargs.get("user_id") == "test-user-id", f"user_id not passed to register: {call_kwargs}"

            # Verify grants.add_grant was called with memory_read
            mock_grants.add_grant.assert_called_once()
            grant_args = mock_grants.add_grant.call_args.args
            assert grant_args[0] == "test-agent-20261029-120000", f"canonical_id mismatch: {grant_args[0]}"
            assert grant_args[1] == "memory_read", f"scope mismatch: {grant_args[1]}"

    @pytest.mark.asyncio
    async def test_registry_row_has_correct_user_id_and_origin(self, client, app, tmp_path):
        """The registry row created during deploy has user_id equal to the
        deploying session's user_id and origin == 'taos-deployed'.
        """
        mock_registry = MagicMock()
        mock_registry._db = True
        captured_register_call = {}

        async def mock_register(**kwargs):
            captured_register_call["kwargs"] = kwargs
            return {
                "canonical_id": "test-agent-20261029-120000",
                "display_name": "Test Agent",
                "framework": "none",
                "user_id": kwargs.get("user_id", ""),
                "origin": kwargs.get("origin", "taos-deployed"),
                "handle": "",
                "role": None,
                "capabilities": "[]",
                "created_ts": "2026-10-29T12:00:00+00:00",
                "revoked_at": None,
                "status": "active",
                "sponsor_contact_id": None,
                "install_id": "",
            }

        mock_registry.register = mock_register
        app.state.agent_registry = mock_registry

        mock_grants = AsyncMock()
        mock_grants.add_grant = AsyncMock()
        mock_grants.list_grants = AsyncMock(return_value=[])
        app.state.agent_grants = mock_grants

        from tinyagentos.agent_registry_store import load_or_create_signing_keypair
        private_pem, public_pem = load_or_create_signing_keypair(tmp_path)
        app.state.agent_registry_keypair = (private_pem, public_pem)

        mock_archive = AsyncMock()
        mock_archive.record = AsyncMock()
        mock_archive.query = AsyncMock(return_value=[{"event_type": "agent_deployed"}])
        app.state.archive = mock_archive

        app.state.llm_proxy = None
        app.state.secrets = None
        app.state.registry = None
        app.state.taosmd_agent_registry = MagicMock()
        app.state.taosmd_agent_registry.register_agent = AsyncMock()
        app.state.notifications = None
        app.state.deploy_tasks = {}
        app.state.idempotency_cache = None
        app.state.cluster_manager = None

        from tinyagentos.config import AppConfig
        app.state.config = AppConfig(config_path=tmp_path / "config.yaml")
        app.state.config.agents = []

        # Set user_id on request state (simulating authenticated session)
        from starlette.requests import Request
        from starlette.datastructures import Headers
        scope = {"type": "http", "method": "POST", "path": "/api/agents/deploy", "headers": Headers({}).raw}
        request = Request(scope)
        request.state.user_id = "test-user-id"

        # We need to patch the request state on the actual request
        with patch("tinyagentos.deployer.deploy_agent", new_callable=AsyncMock) as mock_deploy:
            mock_deploy.return_value = {
                "success": True, "name": "test-agent", "container": "taos-agent-test-agent",
                "ip": "10.0.0.5", "llm_key": "sk-test", "steps": [], "versioning": True, "versioning_error": None
            }
            r = await client.post("/api/agents/deploy", json={"name": "Test Agent", "framework": "none"})
            assert r.status_code == 200

        # Verify register was called with correct user_id and origin
        assert "kwargs" in captured_register_call
        kwargs = captured_register_call["kwargs"]
        assert kwargs.get("user_id") == "test-user-id", f"user_id mismatch: {kwargs.get('user_id')}"
        assert kwargs.get("origin") == "taos-deployed", f"origin mismatch: {kwargs.get('origin')}"

    @pytest.mark.asyncio
    async def test_grants_store_lists_exactly_memory_read(self, client, app, tmp_path):
        """After deploy, the grants store lists exactly memory_read for the
        deployed agent's canonical_id.
        """
        mock_registry = MagicMock()
        mock_registry._db = True
        canonical_id = "test-agent-20261029-120000"

        async def mock_register(**kwargs):
            return {
                "canonical_id": canonical_id,
                "display_name": "Test Agent",
                "framework": "none",
                "user_id": kwargs.get("user_id", ""),
                "origin": kwargs.get("origin", "taos-deployed"),
                "handle": "",
                "role": None,
                "capabilities": "[]",
                "created_ts": "2026-10-29T12:00:00+00:00",
                "revoked_at": None,
                "status": "active",
                "sponsor_contact_id": None,
                "install_id": "",
            }

        mock_registry.register = mock_register
        app.state.agent_registry = mock_registry

        # Use a real grants store to test the actual list_grants
        from tinyagentos.agent_grants_store import AgentGrantsStore
        grants_db = tmp_path / "test_grants.db"
        real_grants = AgentGrantsStore(grants_db)
        await real_grants.init()
        app.state.agent_grants = real_grants

        from tinyagentos.agent_registry_store import load_or_create_signing_keypair
        private_pem, public_pem = load_or_create_signing_keypair(tmp_path)
        app.state.agent_registry_keypair = (private_pem, public_pem)

        mock_archive = AsyncMock()
        mock_archive.record = AsyncMock()
        mock_archive.query = AsyncMock(return_value=[{"event_type": "agent_deployed"}])
        app.state.archive = mock_archive

        app.state.llm_proxy = None
        app.state.secrets = None
        app.state.registry = None
        app.state.taosmd_agent_registry = MagicMock()
        app.state.taosmd_agent_registry.register_agent = AsyncMock()
        app.state.notifications = None
        app.state.deploy_tasks = {}
        app.state.idempotency_cache = None
        app.state.cluster_manager = None

        from tinyagentos.config import AppConfig
        app.state.config = AppConfig(config_path=tmp_path / "config.yaml")
        app.state.config.agents = []

        with patch("tinyagentos.deployer.deploy_agent", new_callable=AsyncMock) as mock_deploy:
            mock_deploy.return_value = {
                "success": True, "name": "test-agent", "container": "taos-agent-test-agent",
                "ip": "10.0.0.5", "llm_key": "sk-test", "steps": [], "versioning": True, "versioning_error": None
            }
            r = await client.post("/api/agents/deploy", json={"name": "Test Agent", "framework": "none"})
            assert r.status_code == 200

        # Verify grants
        grants = await real_grants.list_grants(canonical_id)
        assert len(grants) == 1, f"Expected exactly 1 grant, got {len(grants)}: {grants}"
        assert grants[0]["scope"] == "memory_read", f"Expected memory_read, got {grants[0]['scope']}"
        assert grants[0]["canonical_id"] == canonical_id


class TestDeployerAgentToken:
    """Unit tests for the deployer half: DeployRequest.agent_token -> TAOS_AGENT_TOKEN env."""

    @pytest.mark.asyncio
    async def test_deploy_request_with_agent_token_sets_env(self, tmp_path):
        """DeployRequest(agent_token="x", ...) puts TAOS_AGENT_TOKEN=x in the env."""
        req = DeployRequest(
            name="test-agent",
            framework="none",
            model=None,
            data_dir=tmp_path,
            agent_token="test-agent-token-value",
            extra_config={"agent_controller_port": 6972},
        )

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.auth.AuthManager") as mock_auth:
            mock_auth.return_value.mint_agent_local_token.return_value = "local-token"
            mock_create.return_value = {"success": True, "name": "taos-agent-test-agent"}

            result = await deploy_agent(req)
            assert result["success"] is True

            env = mock_create.call_args.kwargs["env"]
            assert env.get("TAOS_AGENT_TOKEN") == "test-agent-token-value"
            # TAOS_LOCAL_TOKEN should still be present (unchanged)
            assert env.get("TAOS_LOCAL_TOKEN") == "local-token"

    @pytest.mark.asyncio
    async def test_deploy_request_without_agent_token_leaves_env_absent(self, tmp_path):
        """Empty default agent_token leaves TAOS_AGENT_TOKEN absent from env."""
        req = DeployRequest(
            name="test-agent",
            framework="none",
            model=None,
            data_dir=tmp_path,
            extra_config={"agent_controller_port": 6972},
            # agent_token not provided, should default to ""
        )

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.auth.AuthManager") as mock_auth:
            mock_auth.return_value.mint_agent_local_token.return_value = "local-token"
            mock_create.return_value = {"success": True, "name": "taos-agent-test-agent"}

            result = await deploy_agent(req)
            assert result["success"] is True

            env = mock_create.call_args.kwargs["env"]
            assert "TAOS_AGENT_TOKEN" not in env, "TAOS_AGENT_TOKEN should be absent when agent_token is empty"
            # TAOS_LOCAL_TOKEN should still be present (unchanged)
            assert env.get("TAOS_LOCAL_TOKEN") == "local-token"

    @pytest.mark.asyncio
    async def test_deploy_request_explicit_empty_agent_token_leaves_env_absent(self, tmp_path):
        """Explicit agent_token=\"\" leaves TAOS_AGENT_TOKEN absent from env."""
        req = DeployRequest(
            name="test-agent",
            framework="none",
            model=None,
            data_dir=tmp_path,
            agent_token="",
            extra_config={"agent_controller_port": 6972},
        )

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}), \
             patch("tinyagentos.auth.AuthManager") as mock_auth:
            mock_auth.return_value.mint_agent_local_token.return_value = "local-token"
            mock_create.return_value = {"success": True, "name": "taos-agent-test-agent"}

            result = await deploy_agent(req)
            assert result["success"] is True

            env = mock_create.call_args.kwargs["env"]
            assert "TAOS_AGENT_TOKEN" not in env, "TAOS_AGENT_TOKEN should be absent when agent_token is empty string"
            assert env.get("TAOS_LOCAL_TOKEN") == "local-token"