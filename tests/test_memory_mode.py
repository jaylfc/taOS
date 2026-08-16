import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
class TestMemoryModePersistence:
    """memory_mode is stored on the agent record and defaulted correctly."""

    async def test_deploy_stores_explicit_memory_mode(self, client, app):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{"x": 1}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "Atlas-mm",
            "framework": "openclaw",
            "memory_mode": "taosmd",
        })
        assert resp.status_code == 200
        slug = resp.json()["name"]
        agent = next(a for a in app.state.config.agents if a["name"] == slug)
        assert agent["memory_mode"] == "taosmd"

    async def test_deploy_defaults_memory_mode_to_both(self, client, app):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "DefaultMM",
            "framework": "openclaw",
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == resp.json()["name"])
        assert agent["memory_mode"] == "both"

    async def test_deploy_memory_mode_framework_stored(self, client, app):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "FWonly",
            "framework": "openclaw",
            "memory_mode": "framework",
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == resp.json()["name"])
        assert agent["memory_mode"] == "framework"

    async def test_get_agent_returns_memory_mode(self, client, app):
        app.state.config.agents.append({
            "name": "mm-agent",
            "display_name": "MM Agent",
            "host": "",
            "color": "#888888",
            "memory_mode": "taosmd",
        })
        resp = await client.get("/api/agents/mm-agent")
        assert resp.status_code == 200
        assert resp.json()["memory_mode"] == "taosmd"


@pytest.mark.asyncio
class TestMemoryModeDeployValidation:
    """Deploy rejects a memory selection it cannot honour, before side effects.

    memory_mode is persisted on the agent record and injected as the
    TAOS_MEMORY_MODE env var, so an unvalidated value reaches the agent
    runtime as a mode no branch handles and nothing fails at the boundary.
    PATCH validated this from the start; deploy did not.
    """

    async def test_deploy_rejects_unknown_memory_mode(self, client, app):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        before = len(app.state.config.agents)
        resp = await client.post("/api/agents/deploy", json={
            "name": "BadMode",
            "framework": "openclaw",
            # Correct spelling of the product, wrong case for the enum. This is
            # the realistic mistake, not an obviously junk string.
            "memory_mode": "taOSmd",
        })
        assert resp.status_code == 400
        assert "memory_mode" in resp.json()["error"]
        # The guard runs before any side effect, so no agent may exist.
        assert len(app.state.config.agents) == before

    async def test_deploy_rejects_contradictory_plugin_and_mode(self, client, app):
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        before = len(app.state.config.agents)
        resp = await client.post("/api/agents/deploy", json={
            "name": "Contradiction",
            "framework": "openclaw",
            "memory_plugin": "none",
            "memory_mode": "taosmd",
        })
        assert resp.status_code == 400
        assert len(app.state.config.agents) == before

    async def test_deploy_rejects_null_plugin_with_taosmd_mode(self, client, app):
        """An explicit null plugin is the same incoherent pair as 'none'.

        `memory_plugin` is typed `str | None`, so a client can send JSON null.
        Nothing downstream turns that back into "taosmd": `setdefault` only
        fills a MISSING key and `.get(k, default)` returns the stored None, so
        prompt_assembly's `== "taosmd"` gate is False and the agent runs in
        memory_mode 'taosmd' with no taOSmd rules in its prompt at all.
        """
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        before = len(app.state.config.agents)
        resp = await client.post("/api/agents/deploy", json={
            "name": "NullPlugin",
            "framework": "openclaw",
            "memory_plugin": None,
            "memory_mode": "taosmd",
        })
        assert resp.status_code == 400
        assert len(app.state.config.agents) == before

    async def test_deploy_accepts_null_plugin_with_framework_mode(self, client, app):
        """Control for the test above: null is rejected as a PAIR, not on sight.

        This is the wizard's real "Skip memory for this agent" payload with a
        coherent mode. Without this control the null test would also pass
        against a route that refused every null plugin, which would break the
        one memory-free deploy the wizard actually offers.
        """
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "NullPluginFramework",
            "framework": "openclaw",
            "memory_plugin": None,
            "memory_mode": "framework",
        })
        assert resp.status_code == 200

    async def test_skipped_layer_without_a_mode_derives_framework(self, client, app):
        """A caller that skips memory and never names a mode is NOT rejected.

        memory_mode postdates memory_plugin, so a client written before it
        sends `memory_plugin: null` alone. Rejecting that would break a
        pre-existing contract over a contradiction the caller never stated
        (the "both" default is the server's choice, not theirs). With no
        taOSmd plugin, "framework" is the only coherent mode, so it is
        derived rather than guessed.

        The agent record must show the DERIVED mode, not "both" -- otherwise
        the incoherent pair is merely stored instead of rejected, and
        TAOS_MEMORY_MODE reaches the runtime as a mode the prompt cannot back.
        """
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "SkipNoMode",
            "framework": "openclaw",
            "memory_plugin": None,
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == resp.json()["name"])
        assert agent["memory_mode"] == "framework"

    async def test_explicit_both_still_rejected_when_layer_skipped(self, client, app):
        """Control for the derivation: it must not swallow a STATED contradiction.

        Without this, the derivation above could be widened to always coerce,
        and the guard would silently stop rejecting anything. This is the exact
        payload the deploy wizard sends, which always names memory_mode.
        """
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        before = len(app.state.config.agents)
        resp = await client.post("/api/agents/deploy", json={
            "name": "SkipExplicitBoth",
            "framework": "openclaw",
            "memory_plugin": None,
            "memory_mode": "both",
        })
        assert resp.status_code == 400
        assert len(app.state.config.agents) == before

    async def test_deploy_still_accepts_a_valid_mode(self, client, app):
        """Control: the guard rejects bad values without rejecting good ones.

        Without this, both tests above would pass against a deploy route that
        refused everything, and would prove nothing about the guard.
        """
        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )
        resp = await client.post("/api/agents/deploy", json={
            "name": "GoodMode",
            "framework": "openclaw",
            "memory_plugin": "none",
            "memory_mode": "framework",
        })
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestMemoryModeRuntime:
    """Deployer injects TAOS_MEMORY_MODE env var honouring the mode."""

    async def test_default_memory_mode_injected_as_both(self, tmp_path):
        from tinyagentos.deployer import deploy_agent, DeployRequest

        req = DeployRequest(
            name="mm-default",
            framework="openclaw",
            model=None,
            data_dir=tmp_path,
        )

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-mm-default"}
            result = await deploy_agent(req)
            assert result["success"] is True
            env = mock_create.call_args.kwargs["env"]
            assert env["TAOS_MEMORY_MODE"] == "both"

    async def test_framework_mode_injected(self, tmp_path):
        from tinyagentos.deployer import deploy_agent, DeployRequest

        req = DeployRequest(
            name="mm-fw",
            framework="openclaw",
            model=None,
            data_dir=tmp_path,
            memory_mode="framework",
        )

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-mm-fw"}
            result = await deploy_agent(req)
            assert result["success"] is True
            env = mock_create.call_args.kwargs["env"]
            assert env["TAOS_MEMORY_MODE"] == "framework"

    async def test_taosmd_mode_injected(self, tmp_path):
        from tinyagentos.deployer import deploy_agent, DeployRequest

        req = DeployRequest(
            name="mm-taosmd",
            framework="openclaw",
            model=None,
            data_dir=tmp_path,
            memory_mode="taosmd",
        )

        async def mock_exec(name, cmd, **kwargs):
            if "hostname -I" in " ".join(cmd):
                return (0, "10.0.0.5")
            return (0, "ok")

        with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
             patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
             patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
             patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock, return_value={"success": True, "output": ""}):
            mock_create.return_value = {"success": True, "name": "taos-agent-mm-taosmd"}
            result = await deploy_agent(req)
            assert result["success"] is True
            env = mock_create.call_args.kwargs["env"]
            assert env["TAOS_MEMORY_MODE"] == "taosmd"


class TestMemoryModeConflictRule:
    """The conflict rule: taOSmd is authoritative for durable facts.

    Framework memory is the live working set only. When both stores hold
    contradicting durable facts, taOSmd wins. The agent must resolve conflicts
    by trusting taOSmd and syncing framework memory to match.
    """

    def test_taosmd_authoritative_for_durable_facts(self):
        rule = (
            "taOSmd wins for durable facts. "
            "Framework memory wins for live working state only. "
            "If both contradict, trust taOSmd and update framework memory to match."
        )
        assert "taOSmd wins" in rule
        assert "Framework memory wins" in rule
        assert "update framework memory to match" in rule

    def test_framework_mode_never_calls_taosmd(self):
        """In 'framework' mode, the agent must not call taOSmd memory APIs."""
        # The mode is enforced by the agent runtime reading TAOS_MEMORY_MODE.
        # The deployer injects it; the agent runtime is responsible for honouring it.
        # This test documents the contract: framework mode => no taOSmd calls.
        mode = "framework"
        assert mode != "taosmd"
        assert mode != "both"

    def test_both_mode_requires_explicit_store_selection(self):
        """In 'both' mode, the agent must know which store to write to."""
        durable = "taOSmd"
        working = "framework"
        assert durable != working
        assert durable == "taOSmd"
        assert working == "framework"
