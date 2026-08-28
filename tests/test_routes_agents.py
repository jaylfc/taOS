import pytest
from tinyagentos.config import load_config
from tinyagentos.cluster.worker_protocol import WorkerInfo
from tinyagentos.containers.backend import ContainerInfo


@pytest.fixture(autouse=True)
def _default_container_exists(monkeypatch):
    """Default to "container present" for DELETE/archive flows so the
    happy-path tests below don't need to patch container_exists themselves.
    Orphan-specific tests override this with their own monkeypatch.
    """
    async def _exists(name):
        return True
    monkeypatch.setattr("tinyagentos.containers.container_exists", _exists)


@pytest.mark.asyncio
class TestAgentsPage:
    async def test_list_agents_api(self, client):
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "test-agent"

    async def test_add_agent(self, client, tmp_data_dir):
        resp = await client.post("/api/agents", json={
            "name": "new-agent", "host": "10.0.0.5", "qmd_index": "new", "color": "#ff0000",
        })
        assert resp.status_code == 200
        config = load_config(tmp_data_dir / "config.yaml")
        assert len(config.agents) == 2

    async def test_update_agent(self, client, tmp_data_dir):
        resp = await client.put("/api/agents/test-agent", json={"host": "10.0.0.99"})
        assert resp.status_code == 200
        config = load_config(tmp_data_dir / "config.yaml")
        assert config.agents[0]["host"] == "10.0.0.99"

    async def test_delete_agent(self, client, tmp_data_dir, monkeypatch):
        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        config = load_config(tmp_data_dir / "config.yaml")
        assert len(config.agents) == 0
        assert len(config.archived_agents) == 1

    async def test_add_duplicate_name_gets_suffixed(self, client):
        resp = await client.post("/api/agents", json={
            "name": "test-agent", "host": "10.0.0.1", "qmd_index": "dup", "color": "#000",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["display_name"] == "test-agent"
        assert data["name"] == "test-agent-2"


@pytest.mark.asyncio
class TestBulkOperations:
    async def test_bulk_start(self, client):
        resp = await client.post("/api/agents/bulk/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "start"
        assert "results" in data
        assert "test-agent" in data["results"]

    async def test_bulk_stop(self, client, monkeypatch):
        # Mock list_containers + stop_container to avoid incus shell-out
        # in CI and on hosts without a running incus daemon. The new
        # _grace_kill coroutine in bulk_stop_agents calls both, and
        # _bulk_container_op calls stop_container for each agent.
        async def fake_list_containers(prefix="taos-agent-"):
            return [
                ContainerInfo(
                    name="taos-agent-test-agent",
                    status="Stopped",
                    ip=None,
                    memory_mb=0,
                    cpu_cores=0,
                )
            ]

        async def fake_stop_container(name, force=False):
            return {"success": True, "output": ""}

        monkeypatch.setattr(
            "tinyagentos.containers.list_containers", fake_list_containers
        )
        monkeypatch.setattr(
            "tinyagentos.containers.stop_container", fake_stop_container
        )

        resp = await client.post("/api/agents/bulk/stop")
        assert resp.status_code == 200
        assert resp.json()["action"] == "stop"

    async def test_bulk_restart(self, client):
        resp = await client.post("/api/agents/bulk/restart")
        assert resp.status_code == 200
        assert resp.json()["action"] == "restart"


@pytest.mark.asyncio
class TestKillSwitchRunStateGate:
    """Verify that force-kill is gated on container run-state, not just existence."""

    async def test_bulk_stop_skips_force_kill_for_stopped_container(
        self, client, monkeypatch
    ):
        """A stopped container must NOT receive incus stop --force."""
        stopped = ContainerInfo(
            name="taos-agent-test-agent",
            status="Stopped",
            ip=None,
            memory_mb=0,
            cpu_cores=0,
        )

        async def fake_list_containers(prefix="taos-agent-"):
            return [stopped]

        monkeypatch.setattr(
            "tinyagentos.containers.list_containers", fake_list_containers
        )

        force_calls = []

        async def fake_stop(name, force=False):
            if force:
                force_calls.append(name)
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)

        resp = await client.post("/api/agents/bulk/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "stop"
        # The stopped container should not receive a force kill
        assert "taos-agent-test-agent" not in force_calls, (
            f"force-kill should NOT be called on a Stopped container, "
            f"but stop_container(force=True) was called {force_calls}"
        )

    async def test_stop_agent_skips_force_kill_for_stopped_container(
        self, client, monkeypatch
    ):
        """A single stopped agent container must NOT receive incus stop --force."""
        stopped = ContainerInfo(
            name="taos-agent-test-agent",
            status="Stopped",
            ip=None,
            memory_mb=0,
            cpu_cores=0,
        )

        async def fake_list_containers(prefix="taos-agent-"):
            return [stopped]

        monkeypatch.setattr(
            "tinyagentos.containers.list_containers", fake_list_containers
        )

        force_calls = []

        async def fake_stop(name, force=False):
            if force:
                force_calls.append(name)
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)

        resp = await client.post("/api/agents/test-agent/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert "force_killed" in data
        assert data["force_killed"] is False, (
            "force_killed should be False for a Stopped container"
        )
        assert len(force_calls) == 0, (
            f"stop_container(force=True) should NOT be called on a Stopped container, "
            f"but it was called {len(force_calls)} times"
        )

    async def test_bulk_stop_force_kills_running_container(
        self, client, monkeypatch
    ):
        """A running container MUST receive incus stop --force."""
        running = ContainerInfo(
            name="taos-agent-test-agent",
            status="Running",
            ip="10.0.0.5",
            memory_mb=1024,
            cpu_cores=2,
        )

        async def fake_list_containers(prefix="taos-agent-"):
            return [running]

        monkeypatch.setattr(
            "tinyagentos.containers.list_containers", fake_list_containers
        )

        force_calls = []

        async def fake_stop(name, force=False):
            if force:
                force_calls.append(name)
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)

        resp = await client.post("/api/agents/bulk/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "stop"
        assert "taos-agent-test-agent" in force_calls, (
            "force-kill should be called on a Running container"
        )


    async def test_stop_agent_force_kills_running_container(
        self, client, monkeypatch
    ):
        """A single running agent container MUST receive incus stop --force."""
        running = ContainerInfo(
            name="taos-agent-test-agent",
            status="Running",
            ip="10.0.0.5",
            memory_mb=1024,
            cpu_cores=2,
        )

        async def fake_list_containers(prefix="taos-agent-"):
            return [running]

        monkeypatch.setattr(
            "tinyagentos.containers.list_containers", fake_list_containers
        )

        force_calls = []

        async def fake_stop(name, force=False):
            if force:
                force_calls.append(name)
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)

        resp = await client.post("/api/agents/test-agent/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["force_killed"] is True, (
            "force_killed should be True for a Running container"
        )
        assert "taos-agent-test-agent" in force_calls, (
            "force-kill should be called on a Running container via stop_agent"
        )


def _seed_worker(app, name, model_names, status="online"):
    info = WorkerInfo(
        name=name,
        url=f"http://{name}.local:11434",
        hardware={},
        backends=[
            {
                "name": f"ollama@{name}",
                "type": "ollama",
                "url": f"http://{name}.local:11434",
                "capabilities": ["chat"],
                "models": [{"name": m, "size_mb": 0} for m in model_names],
                "status": "ok",
            }
        ],
        models=list(model_names),
        capabilities=["chat"],
        platform="linux",
        status=status,
    )
    app.state.cluster_manager._workers[name] = info
    return info


@pytest.mark.asyncio
class TestDeployRouting:
    async def test_model_not_found_rejects_404(self, client, app):
        resp = await client.post("/api/agents/deploy", json={
            "name": "ghost-agent",
            "framework": "none",
            "model": "does-not-exist-anywhere",
        })
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()

    async def test_worker_hosted_model_unpinned_routes_to_holder(self, client, app):
        _seed_worker(app, "fedora", ["qwen2.5-7b"])
        resp = await client.post("/api/agents/deploy", json={
            "name": "routed-agent",
            "framework": "none",
            "model": "qwen2.5-7b",
        })
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "routed"
        assert data["worker"] == "fedora"
        assert data["available_on"] == ["fedora"]
        config = app.state.config
        assert not any(a["name"] == "routed-agent" for a in config.agents)

    async def test_worker_hosted_model_pinned_to_holder_deploys_remote(self, client, app):
        # Pin-wins: a pin to the worker that holds the model now falls through
        # routing and schedules a remote deploy on that worker, rather than
        # returning a 202 routing stub. With a reachable controller callback
        # host configured, configure_remote_deploy accepts the request.
        app.state.controller_lan_ip = "10.0.0.5"
        _seed_worker(app, "fedora", ["qwen2.5-7b"])
        _seed_worker(app, "arch-box", ["phi3"])
        resp = await client.post("/api/agents/deploy", json={
            "name": "pinned-ok",
            "framework": "none",
            "model": "qwen2.5-7b",
            "target_worker": "fedora",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deploying"
        assert data["name"] == "pinned-ok"

    async def test_worker_hosted_model_pinned_to_wrong_worker_rejects_409(self, client, app):
        _seed_worker(app, "fedora", ["qwen2.5-7b"])
        _seed_worker(app, "arch-box", ["phi3"])
        resp = await client.post("/api/agents/deploy", json={
            "name": "pin-conflict",
            "framework": "none",
            "model": "qwen2.5-7b",
            "target_worker": "arch-box",
        })
        assert resp.status_code == 409
        data = resp.json()
        assert "not on worker" in data["error"]
        assert data["pinned_worker"] == "arch-box"
        assert data["available_on"] == ["fedora"]

    async def test_canonical_host_is_alphabetical_when_multiple_workers_have_model(
        self, client, app
    ):
        _seed_worker(app, "zeta", ["shared-model"])
        _seed_worker(app, "alpha", ["shared-model"])
        _seed_worker(app, "mid", ["shared-model"])
        resp = await client.post("/api/agents/deploy", json={
            "name": "multi-host",
            "framework": "none",
            "model": "shared-model",
        })
        assert resp.status_code == 202
        data = resp.json()
        assert data["worker"] == "alpha"
        assert data["available_on"] == ["alpha", "mid", "zeta"]

    async def test_controller_local_model_falls_through(self, client, app):
        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "local-model", "id": "local-model"}]

        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "local-agent",
            "framework": "none",
            "model": "local-model",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deploying"
        assert data["name"] == "local-agent"

    async def test_cloud_model_falls_through(self, client, app, tmp_data_dir):
        config = app.state.config
        config.backends.append({
            "name": "openai",
            "type": "openai",
            "url": "https://api.openai.com",
            "priority": 10,
            "models": [{"id": "gpt-4o-mini", "name": "GPT-4o mini"}],
        })
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "cloud-agent",
            "framework": "none",
            "model": "gpt-4o-mini",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deploying"


@pytest.mark.asyncio
class TestResumeRoute:
    async def test_resume_clears_paused_flag(self, client, app, tmp_data_dir):
        agent = app.state.config.agents[0]
        assert agent["name"] == "test-agent"
        agent["paused"] = True

        resp = await client.post("/api/agents/test-agent/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resumed"
        assert data["paused"] is False

        from tinyagentos.config import load_config
        config = load_config(tmp_data_dir / "config.yaml")
        assert config.agents[0].get("paused") is False

    async def test_resume_not_found(self, client):
        resp = await client.post("/api/agents/no-such-agent/resume")
        assert resp.status_code == 404

    async def test_resume_already_running(self, client):
        resp = await client.post("/api/agents/test-agent/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] is False


@pytest.mark.asyncio
class TestModelUpdateRoute:
    async def test_model_update_with_reachable_model(self, client, app):
        _seed_worker(app, "gpu-box", ["qwen2.5-7b"])

        resp = await client.post("/api/agents/test-agent/model", json={"model": "qwen2.5-7b"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["model"] == "qwen2.5-7b"

    async def test_model_update_resumes_paused_agent(self, client, app, tmp_data_dir):
        _seed_worker(app, "gpu-box", ["phi3"])
        agent = app.state.config.agents[0]
        agent["paused"] = True

        resp = await client.post("/api/agents/test-agent/model", json={"model": "phi3"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["resumed"] is True
        assert data["paused"] is False if "paused" in data else True

        from tinyagentos.config import load_config
        config = load_config(tmp_data_dir / "config.yaml")
        assert config.agents[0].get("paused") is False

    async def test_model_update_rejects_unreachable_model(self, client, app):
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/test-agent/model", json={"model": "ghost-model"})
        assert resp.status_code == 409
        data = resp.json()
        assert "not reachable" in data["error"]

    async def test_model_update_not_found(self, client):
        resp = await client.post("/api/agents/no-such-agent/model", json={"model": "phi3"})
        assert resp.status_code == 404

    async def test_model_update_empty_model_rejected(self, client):
        resp = await client.post("/api/agents/test-agent/model", json={"model": "   "})
        assert resp.status_code == 400

    async def test_model_update_with_local_model(self, client, app):
        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "local-llm", "id": "local-llm"}]

        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/test-agent/model", json={"model": "local-llm"})
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestDeployPersistence:
    async def test_deploy_persists_model_and_framework(self, client, app, monkeypatch):
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.42",
                    "llm_key": "sk-test", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "test-model", "id": "test-model"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "persistent",
            "framework": "none",
            "model": "test-model",
            "color": "#abcdef",
        })
        assert resp.status_code == 200
        import asyncio
        await asyncio.sleep(0.2)

        detail = await client.get("/api/agents/persistent")
        assert detail.status_code == 200
        agent = detail.json()
        assert agent["model"] == "test-model"
        assert agent["framework"] == "none"
        assert agent["llm_key"] == "sk-test"
        assert agent["status"] == "running"
        assert agent["id"]

    async def test_deploy_accepts_and_persists_emoji(self, client, app, monkeypatch):
        captured: dict = {}

        async def fake_deploy(req):
            captured["emoji"] = req.emoji
            return {"success": True, "name": req.name, "ip": "10.0.0.45",
                    "llm_key": None, "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "test-model", "id": "test-model"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "emoji-agent",
            "framework": "none",
            "model": "test-model",
            "color": "#abcdef",
            "emoji": "\U0001f98a",  # 🦊
        })
        assert resp.status_code == 200
        import asyncio
        await asyncio.sleep(0.2)

        # DeployRequest received the emoji.
        assert captured["emoji"] == "\U0001f98a"

        # GET /api/agents/{name} returns the stored emoji.
        detail = await client.get("/api/agents/emoji-agent")
        assert detail.status_code == 200
        assert detail.json()["emoji"] == "\U0001f98a"

        # GET /api/agents (list) also exposes it.
        listing = await client.get("/api/agents")
        assert listing.status_code == 200
        match = [a for a in listing.json() if a["name"] == "emoji-agent"]
        assert match and match[0].get("emoji") == "\U0001f98a"

    async def test_deploy_emoji_optional(self, client, app, monkeypatch):
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.46",
                    "llm_key": None, "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "test-model", "id": "test-model"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "no-emoji-agent",
            "framework": "none",
            "model": "test-model",
            "color": "#abcdef",
        })
        assert resp.status_code == 200
        import asyncio
        await asyncio.sleep(0.2)

        detail = await client.get("/api/agents/no-emoji-agent")
        assert detail.status_code == 200
        # emoji is optional — stored as None (or omitted) when not provided.
        assert detail.json().get("emoji") is None

    async def test_deploy_creates_dm_channel(self, client, monkeypatch):
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.43",
                    "llm_key": None, "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        resp = await client.post("/api/agents/deploy", json={
            "name": "chatter",
            "framework": "none",
            "color": "#112233",
        })
        assert resp.status_code == 200
        import asyncio
        await asyncio.sleep(0.2)

        detail = await client.get("/api/agents/chatter")
        agent = detail.json()
        channel_id = agent.get("chat_channel_id")
        assert channel_id, "deploy should create a DM channel and save its id"

        channels = await client.get("/api/chat/channels")
        assert channels.status_code == 200
        ch_list = channels.json().get("channels", [])
        assert any(c.get("id") == channel_id and c.get("type") == "dm" for c in ch_list)


@pytest.mark.asyncio
class TestDeployRegistryRegistration:
    """Deploy endpoint must mint a fresh agent_registry canonical_id for every
    deployed agent - identities are minted once per agent and never shared,
    even between agents with the same display name."""

    async def test_deploy_creates_registry_row(self, client, app, monkeypatch):
        import re
        import taosmd.agents as tm_agents
        from unittest.mock import AsyncMock, MagicMock

        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )

        register_calls = []
        canonical_ids = []

        async def fake_register(*, framework, display_name, **kw):
            register_calls.append((framework, display_name))
            cid = f"{display_name.lower().replace(' ', '-')}-20260101-000000"
            canonical_ids.append(cid)
            return {"canonical_id": cid, "framework": framework, "display_name": display_name}

        mock_registry = MagicMock()
        mock_registry.register = fake_register
        mock_registry.get = AsyncMock(return_value=None)
        app.state.agent_registry = mock_registry

        def fake_register_agent(name, **kwargs):
            pass
        monkeypatch.setattr(tm_agents, "register_agent", fake_register_agent)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.42",
                    "llm_key": "sk-test", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "test-model", "id": "test-model"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "RegistryTest",
            "framework": "none",
            "model": "test-model",
        })
        assert resp.status_code == 200
        import asyncio
        await asyncio.sleep(0.2)

        assert len(register_calls) == 1
        assert re.match(r"^[a-z0-9-]+-\d{8}-\d{6}(-[0-9a-f]{2})?$", canonical_ids[0])
        agent = next(a for a in app.state.config.agents if a["name"] == resp.json()["name"])
        assert agent["registry_canonical_id"] == canonical_ids[0]

    async def test_same_display_name_deploys_mint_distinct_identities(self, client, app, monkeypatch):
        """Display names are not unique; a second deploy with the same name is
        a NEW agent (suffixed slug) and must get its own canonical_id, never
        inherit the first agent's identity."""
        import taosmd.agents as tm_agents
        from unittest.mock import AsyncMock, MagicMock

        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )

        register_calls = []

        async def fake_register(*, framework, display_name, **kw):
            register_calls.append((framework, display_name))
            cid = f"registrytwin-20260101-{len(register_calls):06d}"
            return {"canonical_id": cid, "framework": framework, "display_name": display_name}

        mock_registry = MagicMock()
        mock_registry.register = fake_register
        # get() resolves any id to a live row: reusing an existing entry's
        # identity would therefore succeed if the route ever tried it.
        async def fake_get(cid):
            return {"canonical_id": cid}
        mock_registry.get = fake_get
        app.state.agent_registry = mock_registry

        def fake_register_agent(name, **kwargs):
            pass
        monkeypatch.setattr(tm_agents, "register_agent", fake_register_agent)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.42",
                    "llm_key": "sk-test", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "test-model", "id": "test-model"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        import asyncio
        resp1 = await client.post("/api/agents/deploy", json={
            "name": "RegistryTwin",
            "framework": "none",
            "model": "test-model",
        })
        assert resp1.status_code == 200
        await asyncio.sleep(0.2)

        resp2 = await client.post("/api/agents/deploy", json={
            "name": "RegistryTwin",
            "framework": "none",
            "model": "test-model",
        })
        assert resp2.status_code == 200
        await asyncio.sleep(0.2)

        assert len(register_calls) == 2
        twins = [a for a in app.state.config.agents
                 if a.get("display_name") == "RegistryTwin"]
        assert len(twins) == 2
        ids = {a["registry_canonical_id"] for a in twins}
        assert len(ids) == 2, f"identities must be distinct, got {ids}"

    async def test_reserved_name_registration_rejected_as_400(self, client, app, monkeypatch):
        """A name the registry rejects (reserved prefix) is a user error: the
        deploy must return 400 with the registry's message and add no agent."""
        import taosmd.agents as tm_agents
        from unittest.mock import AsyncMock, MagicMock

        app.state.archive = MagicMock(
            record=AsyncMock(), query=AsyncMock(return_value=[{}])
        )

        async def fake_register(*, framework, display_name, **kw):
            raise ValueError(
                f"cannot register agent: name {display_name!r} resolves to "
                "reserved prefix 'admin-'; choose a different name"
            )

        mock_registry = MagicMock()
        mock_registry.register = fake_register
        mock_registry.get = AsyncMock(return_value=None)
        app.state.agent_registry = mock_registry

        def fake_register_agent(name, **kwargs):
            pass
        monkeypatch.setattr(tm_agents, "register_agent", fake_register_agent)

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.42",
                    "llm_key": "sk-test", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "test-model", "id": "test-model"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "Admin",
            "framework": "none",
            "model": "test-model",
        })
        assert resp.status_code == 400
        assert "reserved prefix" in resp.json()["error"]
        assert not any(a.get("display_name") == "Admin"
                       for a in app.state.config.agents)


@pytest.mark.asyncio
class TestAgentArchiveLifecycle:
    async def test_archive_creates_snapshot_not_rename(self, client, monkeypatch):
        """DELETE /api/agents/{name} archives via snapshot; no rename called."""
        stopped = []
        snapshots_created = []

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.44",
                    "llm_key": "sk-archive-test", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            stopped.append(name)
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            snapshots_created.append((name, snapshot_name))
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        await client.post("/api/agents/deploy", json={"name": "archiver", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)

        resp = await client.delete("/api/agents/archiver")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archived"
        assert data["name"] == "archiver"
        assert data["snapshot_name"].startswith("taos-archive-")

        assert "taos-agent-archiver" in stopped
        assert len(snapshots_created) == 1
        assert snapshots_created[0][0] == "taos-agent-archiver"
        assert snapshots_created[0][1].startswith("taos-archive-")

        live = (await client.get("/api/agents")).json()
        assert not any(a["name"] == "archiver" for a in live)

        archived = (await client.get("/api/agents/archived")).json()
        assert any(a["original"]["name"] == "archiver" for a in archived)
        assert archived[-1]["snapshot_name"].startswith("taos-archive-")

    async def test_delete_archives_agent(self, client, monkeypatch):
        """DELETE /api/agents/{name} archives instead of destroying."""
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.44",
                    "llm_key": "sk-archive-test", "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        await client.post("/api/agents/deploy", json={"name": "archiver2", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)

        resp = await client.delete("/api/agents/archiver2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archived"

        live = (await client.get("/api/agents")).json()
        assert not any(a["name"] == "archiver2" for a in live)
        archived = (await client.get("/api/agents/archived")).json()
        assert any(a["original"]["name"] == "archiver2" for a in archived)

    async def test_archive_aborts_when_snapshot_fails(self, client, app, monkeypatch):
        """If snapshot_create fails, config must NOT move to archived_agents."""
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.82",
                    "llm_key": None, "steps": [], "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": False, "output": "Error: pool storage error"}

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        await client.post("/api/agents/deploy", json={"name": "stuck", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)

        resp = await client.delete("/api/agents/stuck")
        assert resp.status_code == 500
        assert "could not create snapshot" in resp.json()["error"]

        live = (await client.get("/api/agents")).json()
        assert any(a["name"] == "stuck" for a in live)
        archived = (await client.get("/api/agents/archived")).json()
        assert not any(a.get("archived_slug") == "stuck" for a in archived)

    async def test_restore_uses_snapshot_restore(self, client, monkeypatch):
        """POST /api/agents/archived/{id}/restore calls snapshot_restore."""
        restored_snapshots = []

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.50",
                    "llm_key": "sk-x", "steps": [], "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_snapshot_restore(name, snapshot_name):
            restored_snapshots.append((name, snapshot_name))
            return {"success": True, "output": ""}

        async def fake_start(name):
            return {"success": True, "output": ""}

        async def fake_set_env(name, key, value):
            return {"success": True, "output": ""}

        async def fake_exec(name, cmd, timeout=300):
            return (0, "")

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        monkeypatch.setattr("tinyagentos.containers.snapshot_restore", fake_snapshot_restore)
        monkeypatch.setattr("tinyagentos.containers.start_container", fake_start)
        monkeypatch.setattr("tinyagentos.containers.set_env", fake_set_env)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.post("/api/agents/deploy", json={"name": "snaprest", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)
        await client.delete("/api/agents/snaprest")

        archived = (await client.get("/api/agents/archived")).json()
        archive_id = archived[0]["id"]
        stored_snapshot = archived[0]["snapshot_name"]

        resp = await client.post(f"/api/agents/archived/{archive_id}/restore")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restored"

        assert len(restored_snapshots) == 1
        assert restored_snapshots[0][0] == "taos-agent-snaprest"
        assert restored_snapshots[0][1] == stored_snapshot

    async def test_restore_slug_collision_renames_after_restore(self, client, app, monkeypatch):
        """When slug collides, order is: snapshot_restore -> rename -> start."""
        calls = []

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.50",
                    "llm_key": "sk-x", "steps": [], "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_snapshot_restore(name, snapshot_name):
            calls.append(("restore", name, snapshot_name))
            return {"success": True, "output": ""}

        async def fake_rename(old, new):
            calls.append(("rename", old, new))
            return {"success": True, "output": ""}

        async def fake_start(name):
            calls.append(("start", name))
            return {"success": True, "output": ""}

        async def fake_set_env(name, key, value):
            return {"success": True, "output": ""}

        async def fake_exec(name, cmd, timeout=300):
            return (0, "")

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        monkeypatch.setattr("tinyagentos.containers.snapshot_restore", fake_snapshot_restore)
        monkeypatch.setattr("tinyagentos.containers.rename_container", fake_rename)
        monkeypatch.setattr("tinyagentos.containers.start_container", fake_start)
        monkeypatch.setattr("tinyagentos.containers.set_env", fake_set_env)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.post("/api/agents/deploy", json={"name": "rest", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)
        await client.delete("/api/agents/rest")

        await client.post("/api/agents/deploy", json={"name": "rest", "framework": "none"})
        await asyncio.sleep(0.2)

        archived = (await client.get("/api/agents/archived")).json()
        archive_id = archived[0]["id"]

        calls.clear()
        resp = await client.post(f"/api/agents/archived/{archive_id}/restore")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "rest-2"

        op_names = [c[0] for c in calls]
        assert op_names.index("restore") < op_names.index("rename")
        assert op_names.index("rename") < op_names.index("start")

    async def test_restore_env_rewrite_uses_incus_config_set(self, client, app, monkeypatch):
        """When proxy is running, set_env is called with OPENAI_API_KEY=<new>."""
        env_calls = []

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.56",
                    "llm_key": "sk-old", "steps": [], "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_snapshot_restore(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_start(name):
            return {"success": True, "output": ""}

        async def fake_set_env(name, key, value):
            env_calls.append((name, key, value))
            return {"success": True, "output": ""}

        async def fake_exec(name, cmd, timeout=300):
            return (0, "")

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        monkeypatch.setattr("tinyagentos.containers.snapshot_restore", fake_snapshot_restore)
        monkeypatch.setattr("tinyagentos.containers.start_container", fake_start)
        monkeypatch.setattr("tinyagentos.containers.set_env", fake_set_env)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        proxy = app.state.llm_proxy
        monkeypatch.setattr(proxy, "is_running", lambda: True)
        async def fake_create_agent_key(name, models=None, budget_duration=None):
            return "sk-incus-key"
        monkeypatch.setattr(proxy, "create_agent_key", fake_create_agent_key)

        await client.post("/api/agents/deploy", json={"name": "envtest2", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)
        await client.delete("/api/agents/envtest2")

        archived = (await client.get("/api/agents/archived")).json()
        archive_id = archived[0]["id"]

        resp = await client.post(f"/api/agents/archived/{archive_id}/restore")
        assert resp.status_code == 200
        assert resp.json()["new_llm_key"] is True

        assert any(
            k == "OPENAI_API_KEY" and v == "sk-incus-key"
            for _, k, v in env_calls
        ), f"set_env calls: {env_calls}"

    async def test_purge_destroys_container_and_snapshots(self, client, monkeypatch):
        """DELETE /api/agents/archived/{id} calls destroy_container once."""
        destroyed = []

        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.60",
                    "llm_key": None, "steps": [], "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_destroy(name):
            destroyed.append(name)
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        monkeypatch.setattr("tinyagentos.containers.destroy_container", fake_destroy)

        await client.post("/api/agents/deploy", json={"name": "purgeable", "framework": "none"})
        import asyncio
        await asyncio.sleep(0.2)
        await client.delete("/api/agents/purgeable")

        archived = (await client.get("/api/agents/archived")).json()
        archive_id = archived[0]["id"]

        resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "purged"
        # incus delete on taos-agent-purgeable also destroys all snapshots
        assert "taos-agent-purgeable" in destroyed

        archived2 = (await client.get("/api/agents/archived")).json()
        assert archived2 == []

    async def test_archive_target_pool_no_export_path(self, client, monkeypatch):
        """Default archive.target=pool: — no export path recorded."""
        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["export_path"] is None

        archived = (await client.get("/api/agents/archived")).json()
        assert archived[0]["export_path"] is None

    async def test_archive_target_path_exports_tarball(self, client, app, tmp_data_dir, monkeypatch):
        """When archive.target=path:..., incus export is invoked."""
        incus_export_calls = []

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_run(cmd, timeout=120):
            if len(cmd) >= 2 and cmd[1] == "export":
                incus_export_calls.append(list(cmd))
                import os
                out_file = cmd[-1]
                os.makedirs(os.path.dirname(out_file), exist_ok=True)
                open(out_file, "w").close()
                return (0, "")
            return (0, "")

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        monkeypatch.setattr("tinyagentos.containers._run", fake_run)

        app.state.config.archive["target"] = f"path:{tmp_data_dir}/exports"

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200

        assert len(incus_export_calls) == 1
        assert incus_export_calls[0][0] == "incus"
        assert incus_export_calls[0][1] == "export"


@pytest.mark.asyncio
class TestArchivedChatPersistence:
    """Archive preserves chat messages; restore re-imports; purge deletes all."""

    async def _setup_agent_with_channel(self, client, monkeypatch, slug="chat-agent"):
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.70",
                    "llm_key": None, "steps": [], "container": f"taos-agent-{req.name}"}

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        deploy_resp = await client.post("/api/agents/deploy", json={"name": slug, "framework": "none"})
        assert deploy_resp.status_code == 200
        import asyncio
        await asyncio.sleep(0.2)

        app = client._transport.app
        ch_store = app.state.chat_channels
        msg_store = app.state.chat_messages

        ch = await ch_store.create_channel(
            name=f"dm-{slug}", type="dm", created_by="user",
            members=[slug, "user"],
        )
        config = app.state.config
        for a in config.agents:
            if a["name"] == slug:
                a["chat_channel_id"] = ch["id"]
                break
        from tinyagentos.config import save_config_locked
        await save_config_locked(config, config.config_path)

        m1 = await msg_store.send_message(ch["id"], slug, "agent", "hello from agent")
        m2 = await msg_store.send_message(ch["id"], "user", "user", "hi agent")

        return ch, [m1, m2]

    async def test_archive_writes_chat_export(self, client, monkeypatch, tmp_data_dir):
        """Archive writes chat-export.jsonl into archive/<slug-ts>/chat/."""
        ch, msgs = await self._setup_agent_with_channel(client, monkeypatch)

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        resp = await client.delete("/api/agents/chat-agent")
        assert resp.status_code == 200

        app = client._transport.app
        config = app.state.config
        entry = config.archived_agents[-1]
        archive_base = tmp_data_dir / entry["archive_dir"]

        export_path = archive_base / "chat" / "chat-export.jsonl"
        assert export_path.exists(), f"chat-export.jsonl missing at {export_path}"

        import json
        lines = [json.loads(l) for l in export_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        contents = {l["content"] for l in lines}
        assert "hello from agent" in contents
        assert "hi agent" in contents

        ch_store = app.state.chat_channels
        updated_ch = await ch_store.get_channel(ch["id"])
        s = updated_ch["settings"]
        assert s.get("archived") is True
        assert "archived_at" in s
        assert s.get("archived_agent_id") == entry["id"]
        assert s.get("archived_agent_slug") == "chat-agent"

    async def test_restore_reimports_messages_and_unflags_channel(
        self, client, monkeypatch, tmp_data_dir
    ):
        """Restore: missing messages come back, channel is unflagged."""
        ch, msgs = await self._setup_agent_with_channel(client, monkeypatch, slug="restore-agent")

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_snapshot_restore(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_start(name):
            return {"success": True, "output": ""}

        async def fake_set_env(name, key, value):
            return {"success": True, "output": ""}

        async def fake_exec(name, cmd, timeout=300):
            return (0, "")

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        monkeypatch.setattr("tinyagentos.containers.snapshot_restore", fake_snapshot_restore)
        monkeypatch.setattr("tinyagentos.containers.start_container", fake_start)
        monkeypatch.setattr("tinyagentos.containers.set_env", fake_set_env)
        monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

        await client.delete("/api/agents/restore-agent")

        app = client._transport.app
        config = app.state.config
        entry = config.archived_agents[-1]
        archive_id = entry["id"]

        msg_store = app.state.chat_messages
        await msg_store.delete_channel_messages(ch["id"])
        remaining = await msg_store.get_messages(ch["id"], limit=100)
        assert len(remaining) == 0

        resp = await client.post(f"/api/agents/archived/{archive_id}/restore")
        assert resp.status_code == 200

        reimported = await msg_store.get_messages(ch["id"], limit=100)
        assert len(reimported) == 2
        contents = {m["content"] for m in reimported}
        assert "hello from agent" in contents

        ch_store = app.state.chat_channels
        updated_ch = await ch_store.get_channel(ch["id"])
        assert updated_ch["settings"].get("archived") is False

    async def test_restore_is_idempotent(self, client, monkeypatch, tmp_data_dir):
        """Re-importing the same messages twice does not create duplicates."""
        ch, msgs = await self._setup_agent_with_channel(client, monkeypatch, slug="idem-agent")

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        await client.delete("/api/agents/idem-agent")

        app = client._transport.app
        config = app.state.config
        entry = config.archived_agents[-1]
        archive_base = tmp_data_dir / entry["archive_dir"]
        export_path = archive_base / "chat" / "chat-export.jsonl"
        assert export_path.exists()

        import json
        msg_store = app.state.chat_messages
        lines = [json.loads(l) for l in export_path.read_text().splitlines() if l.strip()]
        for _ in range(2):
            for line in lines:
                await msg_store.ensure_message(line)

        all_msgs = await msg_store.get_messages(ch["id"], limit=100)
        assert len(all_msgs) == 2

    async def test_purge_deletes_channel_and_messages(
        self, client, monkeypatch, tmp_data_dir
    ):
        """Purge removes messages, channel, and archive dir."""
        ch, msgs = await self._setup_agent_with_channel(client, monkeypatch, slug="purge-agent")

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            return {"success": True, "output": ""}

        async def fake_destroy(name):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)
        monkeypatch.setattr("tinyagentos.containers.destroy_container", fake_destroy)

        await client.delete("/api/agents/purge-agent")

        app = client._transport.app
        config = app.state.config
        entry = config.archived_agents[-1]
        archive_id = entry["id"]

        resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "purged"

        ch_store = app.state.chat_channels
        assert await ch_store.get_channel(ch["id"]) is None

        msg_store = app.state.chat_messages
        remaining = await msg_store.get_messages(ch["id"], limit=100)
        assert len(remaining) == 0

        archive_base = tmp_data_dir / entry["archive_dir"]
        assert not archive_base.exists()


@pytest.mark.asyncio
class TestOrphanAgentDeletion:
    """DELETE /api/agents/{name} must tolerate orphan config rows where the
    LXC container was never fully created (failed deploy left only config)."""

    async def test_delete_agent_with_missing_container_succeeds(
        self, client, tmp_data_dir, monkeypatch
    ):
        """Orphan with no chat + no trace is hard-deleted; no 500."""
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        async def fake_stop(name, force=False):
            raise AssertionError("stop_container should not be called for orphans")

        async def fake_snapshot_create(name, snapshot_name):
            raise AssertionError("snapshot_create should not be called for orphans")

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200

        config = load_config(tmp_data_dir / "config.yaml")
        assert not any(a["name"] == "test-agent" for a in config.agents)

    async def test_delete_agent_with_missing_container_skips_snapshot(
        self, client, monkeypatch
    ):
        """Recording mock: snapshot_create and stop_container never invoked."""
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        stop_calls = []
        snap_calls = []

        async def fake_stop(name, force=False):
            stop_calls.append(name)
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            snap_calls.append((name, snapshot_name))
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200

        assert stop_calls == []
        assert snap_calls == []

    async def test_delete_orphan_hard_deletes_when_no_history(
        self, client, tmp_data_dir, monkeypatch
    ):
        """No chat_channel_id and no trace dir -> hard-delete, no tombstone."""
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"

        config = load_config(tmp_data_dir / "config.yaml")
        assert not any(a["name"] == "test-agent" for a in config.agents)
        # Hard-delete: no archive tombstone created.
        assert not any(
            a.get("archived_slug") == "test-agent" for a in config.archived_agents
        )

    async def test_delete_orphan_creates_tombstone_when_trace_history_exists(
        self, client, tmp_data_dir, monkeypatch
    ):
        """Trace dir present -> keep a tombstone (no snapshot) so user can purge."""
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        # Seed a trace dir for the agent so the orphan path records a tombstone.
        trace_dir = tmp_data_dir / "trace" / "test-agent"
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "2026-04-17T13.db").write_text("")

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archived"
        assert data["snapshot_name"] is None

        config = load_config(tmp_data_dir / "config.yaml")
        assert not any(a["name"] == "test-agent" for a in config.agents)
        tombstones = [a for a in config.archived_agents if a.get("archived_slug") == "test-agent"]
        assert len(tombstones) == 1
        assert tombstones[0]["snapshot_name"] is None

    async def test_purge_archived_with_missing_snapshot_succeeds(
        self, client, tmp_data_dir, monkeypatch
    ):
        """Tombstone (snapshot_name=None) purge returns 200 and removes record."""
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        # Seed trace history so DELETE yields a tombstone.
        trace_dir = tmp_data_dir / "trace" / "test-agent"
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "bucket.db").write_text("")

        destroyed = []

        async def fake_destroy(name):
            # Even called for tombstones; must not raise — container is gone.
            destroyed.append(name)
            return {"success": False, "output": "Error: Not Found"}

        monkeypatch.setattr("tinyagentos.containers.destroy_container", fake_destroy)

        del_resp = await client.delete("/api/agents/test-agent")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "archived"

        archived = (await client.get("/api/agents/archived")).json()
        assert len(archived) == 1
        archive_id = archived[0]["id"]
        assert archived[0]["snapshot_name"] is None

        purge_resp = await client.delete(f"/api/agents/archived/{archive_id}")
        assert purge_resp.status_code == 200
        assert purge_resp.json()["status"] == "purged"

        archived_after = (await client.get("/api/agents/archived")).json()
        assert archived_after == []


@pytest.mark.asyncio
class TestDeleteResolvesOrphanedContainer:
    """DELETE must not orphan a real container when the record lost its link.

    Reproduces Jay's live bug: a 'test' agent whose container_name was null but
    whose legacy ``taos-test`` container was still running. Delete must resolve
    and archive that container instead of hard-deleting the bare row.
    """

    async def test_null_link_resolves_and_archives_legacy_container(
        self, client, tmp_data_dir, monkeypatch
    ):
        # The derived ``taos-agent-test-agent`` does not exist...
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        # ...but a legacy ``taos-test-agent`` container is live in some project.
        async def fake_list_all(*_a, **_k):
            return [{"name": "taos-test-agent", "project": "user-999"}]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list_all
        )

        stop_calls = []
        snap_calls = []

        async def fake_stop(name, force=False):
            stop_calls.append(name)
            return {"success": True, "output": ""}

        async def fake_snapshot_create(name, snapshot_name):
            snap_calls.append((name, snapshot_name))
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archived"
        # The resolved legacy container is the one snapshotted, not the derived name.
        assert stop_calls == ["taos-test-agent"]
        assert snap_calls and snap_calls[0][0] == "taos-test-agent"

        config = load_config(tmp_data_dir / "config.yaml")
        assert not any(a["name"] == "test-agent" for a in config.agents)
        entry = next(
            a for a in config.archived_agents if a.get("archived_slug") == "test-agent"
        )
        # container_name records the REAL (legacy) container so restore/purge act on it.
        assert entry["container_name"] == "taos-test-agent"
        assert entry["snapshot_name"] is not None

    async def test_truly_container_less_row_still_hard_deletes(
        self, client, tmp_data_dir, monkeypatch
    ):
        """When NO container exists anywhere, the bare row is hard-deleted."""
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        async def fake_list_all(*_a, **_k):
            return []  # nothing on the host
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list_all
        )

        async def fake_snapshot_create(name, snapshot_name):
            raise AssertionError("no container -> snapshot must not run")
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot_create)

        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        config = load_config(tmp_data_dir / "config.yaml")
        assert not any(a["name"] == "test-agent" for a in config.agents)
        assert not any(
            a.get("archived_slug") == "test-agent" for a in config.archived_agents
        )


@pytest.mark.asyncio
class TestDeployMemoryConfig:
    """Deploy endpoint should accept and persist memory_plugin + memory_config."""

    async def test_deploy_with_null_memory_plugin_accepted(self, client, app):
        """memory_plugin: null skips taosmd for this agent."""
        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "phi3", "id": "phi3"}]

        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "no-memory-agent",
            "framework": "none",
            "model": "phi3",
            "memory_plugin": None,
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == "no-memory-agent")
        # None is coerced to empty string by Pydantic default — accept both
        assert agent.get("memory_plugin") in (None, "", "taosmd")

    async def test_deploy_with_memory_config_persisted(self, client, app):
        """memory_config dict is stored on the agent record."""
        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "phi3", "id": "phi3"}]

        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        mem_cfg = {"device_id": "local", "tier_id": "standard"}
        resp = await client.post("/api/agents/deploy", json={
            "name": "memory-config-agent",
            "framework": "none",
            "model": "phi3",
            "memory_plugin": "taosmd",
            "memory_config": mem_cfg,
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == "memory-config-agent")
        assert agent.get("memory_config") == mem_cfg

    async def test_deploy_without_memory_config_defaults_to_none(self, client, app):
        """Omitting memory_config results in None on the agent record (global default used)."""
        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "phi3", "id": "phi3"}]

        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        resp = await client.post("/api/agents/deploy", json={
            "name": "default-memory-agent",
            "framework": "none",
            "model": "phi3",
        })
        assert resp.status_code == 200
        agent = next(a for a in app.state.config.agents if a["name"] == "default-memory-agent")
        assert agent.get("memory_config") is None


@pytest.mark.asyncio
class TestDeployRamPreflight:
    """Pre-flight RAM check — low-RAM hosts reject deploy before mutating state (#384)."""

    async def _mock_deploy_passthrough(self, monkeypatch):
        """Allow deploy to run (patched out so no real container work)."""
        async def fake_deploy(req):
            return {"success": True, "name": req.name, "ip": "10.0.0.1",
                    "llm_key": None, "steps": ["deployment_complete"],
                    "container": f"taos-agent-{req.name}"}
        monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)

    async def test_low_ram_rejects_deploy(self, client, app, monkeypatch):
        """Host with 2GB RAM should be rejected for all frameworks."""
        class _FakeHW:
            ram_mb = 2048  # 2GB — below every framework's minimum
        app.state.hardware_profile = _FakeHW()

        resp = await client.post("/api/agents/deploy", json={
            "name": "low-ram-agent",
            "framework": "openclaw",
            "model": "phi3",
        })
        assert resp.status_code == 400
        body = resp.json()
        assert "2.0 GB RAM" in body["error"]
        assert "openclaw" in body["error"]
        assert "needs at least" in body["error"]
        assert body["ram_mb"] == 2048
        assert body["framework"] == "openclaw"

    async def test_adequate_ram_passes(self, client, app, monkeypatch):
        """Host with 8GB RAM should pass the pre-flight check."""
        await self._mock_deploy_passthrough(monkeypatch)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "phi3", "id": "phi3"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        class _FakeHW:
            ram_mb = 8192  # 8GB — well above any framework minimum
        app.state.hardware_profile = _FakeHW()

        resp = await client.post("/api/agents/deploy", json={
            "name": "adequate-ram-agent",
            "framework": "openclaw",
            "model": "phi3",
        })
        assert resp.status_code == 200

    async def test_no_framework_skips_ram_check(self, client, app, monkeypatch):
        """framework='none' should skip the RAM check entirely."""
        await self._mock_deploy_passthrough(monkeypatch)

        class _FakeCatalog:
            def all_models(self, capability=None):
                return [{"name": "phi3", "id": "phi3"}]
        app.state.backend_catalog = _FakeCatalog()
        app.state.cluster_manager._workers.clear()

        class _FakeHW:
            ram_mb = 1024  # 1GB — would fail check if it ran
        app.state.hardware_profile = _FakeHW()

        resp = await client.post("/api/agents/deploy", json={
            "name": "no-framework-agent",
            "framework": "none",
            "model": "phi3",
        })
        assert resp.status_code == 200

    async def test_unknown_framework_still_checked_before_ram(self, client, app, monkeypatch):
        """Unknown framework should fail at framework validation, not RAM check."""
        resp = await client.post("/api/agents/deploy", json={
            "name": "bad-fw-agent",
            "framework": "nonexistent-framework",
            "model": "phi3",
        })
        assert resp.status_code == 400
        body = resp.json()
        assert "Unknown framework" in body["error"]


@pytest.mark.asyncio
class TestAgentBudgetRoutes:
    """Agent governance Slice 2 — per-agent LLM budget admin API."""

    async def test_get_budget_defaults_when_unset(self, client):
        resp = await client.get("/api/agents/test-agent/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_budget_usd"] is None
        assert data["spend_usd"] == 0

    async def test_get_budget_not_found_agent(self, client):
        resp = await client.get("/api/agents/no-such-agent/budget")
        assert resp.status_code == 404

    async def test_put_budget_sets_cap(self, client):
        resp = await client.put("/api/agents/test-agent/budget", json={"max_budget_usd": 25.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_budget_usd"] == 25.0
        assert data["spend_usd"] == 0

        resp2 = await client.get("/api/agents/test-agent/budget")
        assert resp2.json()["max_budget_usd"] == 25.0

    async def test_put_budget_null_clears_cap(self, client):
        await client.put("/api/agents/test-agent/budget", json={"max_budget_usd": 25.0})
        resp = await client.put("/api/agents/test-agent/budget", json={"max_budget_usd": None})
        assert resp.status_code == 200
        assert resp.json()["max_budget_usd"] is None

    async def test_put_budget_rejects_negative(self, client):
        resp = await client.put("/api/agents/test-agent/budget", json={"max_budget_usd": -5.0})
        assert resp.status_code == 400

    async def test_put_budget_rejects_non_finite(self, client):
        # NaN/Infinity would slip past a bare "< 0" check and disable the cap
        # (spend >= NaN is always False), so they must be rejected with 400.
        for bad in ("NaN", "Infinity", "-Infinity"):
            resp = await client.put(
                "/api/agents/test-agent/budget",
                content=f'{{"max_budget_usd": {bad}}}',
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 400, f"{bad} was not rejected"

    async def test_put_budget_not_found_agent(self, client):
        resp = await client.put("/api/agents/no-such-agent/budget", json={"max_budget_usd": 10.0})
        assert resp.status_code == 404

    async def test_reset_zeroes_spend_keeps_cap(self, client, app, tmp_data_dir):
        await client.put("/api/agents/test-agent/budget", json={"max_budget_usd": 10.0})

        from tinyagentos.agent_budget_store import AgentBudgetStore, default_budget_path
        store = AgentBudgetStore(default_budget_path(tmp_data_dir))
        store.add_spend("test-agent", 7.5)

        resp = await client.post("/api/agents/test-agent/budget/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["spend_usd"] == 0
        assert data["max_budget_usd"] == 10.0

    async def test_reset_not_found_agent(self, client):
        resp = await client.post("/api/agents/no-such-agent/budget/reset")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestAgentWakeBudget:
    async def test_get_wake_budget_defaults(self, client):
        resp = await client.get("/api/agents/test-agent/wake-budget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "test-agent"
        assert data["budget"] == 2
        assert data["consumed"] == 0
        assert data["remaining"] == 2
        assert data["date"] == _today_str()
        assert data["next_wake_epoch"] is not None

    async def test_wake_budget_reports_enforced_project_counter(
        self, client, app, tmp_data_dir, monkeypatch
    ):
        from tinyagentos.agent_heartbeat import _heartbeat_tick

        agent = next(a for a in app.state.config.agents if a["name"] == "test-agent")
        agent["id"] = "test-agent"
        agent["status"] = "running"
        app.state.config.server["agent_heartbeat_enabled"] = True

        project = await app.state.project_store.create_project(
            name="proj-1", slug="proj-1", created_by="test",
        )
        await app.state.project_task_store.create_task(
            project_id=project["id"],
            title="ready task",
            created_by="test",
            assignee_id="test-agent",
        )

        async def fake_wake(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "tinyagentos.agent_heartbeat._wake_agent_with_task", fake_wake
        )

        await _heartbeat_tick(app.state)

        resp = await client.get("/api/agents/test-agent/wake-budget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["consumed"] == 1

    async def test_wake_budget_reports_per_project_override(
        self, client, app, tmp_data_dir, monkeypatch
    ):
        from tinyagentos.agent_heartbeat import _heartbeat_tick

        agent = next(a for a in app.state.config.agents if a["name"] == "test-agent")
        agent["id"] = "test-agent"
        agent["status"] = "running"
        app.state.config.server["agent_heartbeat_enabled"] = True

        project = await app.state.project_store.create_project(
            name="proj-pp", slug="proj-pp", created_by="test",
        )
        app.state.config.wake_budget = {
            "global_default": 2,
            "per_agent": {},
            "per_project": {project["id"]: 1},
        }
        await app.state.project_task_store.create_task(
            project_id=project["id"],
            title="ready task",
            created_by="test",
            assignee_id="test-agent",
        )

        async def fake_wake(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "tinyagentos.agent_heartbeat._wake_agent_with_task", fake_wake
        )

        await _heartbeat_tick(app.state)

        resp = await client.get("/api/agents/test-agent/wake-budget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["budget"] == 1
        assert data["consumed"] == 1
        assert data["remaining"] == 0

        resp2 = await client.get("/api/observatory/wake-budget")
        assert resp2.status_code == 200
        data2 = resp2.json()
        agent_row = next(a for a in data2["agents"] if a["agent_id"] == "test-agent")
        assert agent_row["budget"] == 1
        assert agent_row["consumed"] == 1
        assert agent_row["remaining"] == 0

    async def test_get_wake_budget_not_found(self, client):
        resp = await client.get("/api/agents/no-such-agent/wake-budget")
        assert resp.status_code == 404


def _today_str() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
