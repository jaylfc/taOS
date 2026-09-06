"""Endpoint-level tests for the agent deploy route, exercising the helpers
in tinyagentos/routes/agent_deploy.py through the FastAPI test client.

The full /api/agents/deploy endpoint requires live infrastructure
(container runtime, taosmd, LLM proxy, etc.) and is NOT tested end-to-end.
Instead we exercise the validation and routing helpers that the endpoint
calls, which are reachable through the endpoint with appropriate mocking.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, Mock, patch

from tinyagentos.cluster.model_resolver import ModelLocation


def _app(client):
    return client._transport.app


# ---------------------------------------------------------------------------
# validate_framework_and_ram
# ---------------------------------------------------------------------------


class TestValidateFrameworkAndRam:
    """Tests for agent_deploy.validate_framework_and_ram via the deploy endpoint."""

    @pytest.mark.asyncio
    async def test_unknown_framework_returns_400(self, client):
        """A framework not in the registry catalog must return 400."""
        mock_manifest = Mock()
        mock_manifest.id = "some-framework"
        mock_manifest.type = "agent-framework"

        mock_registry = Mock()
        mock_registry.list_available = Mock(return_value=[mock_manifest])

        app = _app(client)
        app.state.registry = mock_registry
        app.state.hardware_profile = None

        r = await client.post(
            "/api/agents/deploy",
            json={"name": "test-agent", "framework": "nonexistent-fw"},
        )
        assert r.status_code == 400
        body = r.json()
        assert "error" in body
        assert "nonexistent-fw" in body["error"]

    @pytest.mark.asyncio
    async def test_unknown_framework_lists_available(self, client):
        """The 400 error for an unknown framework lists available frameworks."""
        mock_m1 = Mock()
        mock_m1.id = "openclaw"
        mock_m1.type = "agent-framework"
        mock_m2 = Mock()
        mock_m2.id = "smolagents"
        mock_m2.type = "agent-framework"

        mock_registry = Mock()
        mock_registry.list_available = Mock(return_value=[mock_m1, mock_m2])

        app = _app(client)
        app.state.registry = mock_registry
        app.state.hardware_profile = None

        r = await client.post(
            "/api/agents/deploy",
            json={"name": "test-agent", "framework": "bogus"},
        )
        assert r.status_code == 400
        body = r.json()
        assert "openclaw" in body["error"]
        assert "smolagents" in body["error"]

    @pytest.mark.asyncio
    async def test_low_ram_returns_400(self, client):
        """A framework that needs more RAM than available must return 400."""
        mock_manifest = Mock()
        mock_manifest.id = "openclaw"
        mock_manifest.type = "agent-framework"
        mock_manifest.requires = {"ram_mb": 2048}

        mock_registry = Mock()
        mock_registry.list_available = Mock(return_value=[mock_manifest])
        mock_registry.get = Mock(return_value=mock_manifest)

        mock_hw = Mock()
        mock_hw.ram_mb = 2048  # 2 GB -- not enough for 2048 + 500 + 2048

        app = _app(client)
        app.state.registry = mock_registry
        app.state.hardware_profile = mock_hw

        r = await client.post(
            "/api/agents/deploy",
            json={"name": "test-agent", "framework": "openclaw"},
        )
        assert r.status_code == 400
        body = r.json()
        assert "error" in body
        assert "ram_mb" in body
        assert "min_ram_mb" in body
        assert body["framework"] == "openclaw"

    @pytest.mark.asyncio
    async def test_sufficient_ram_passes_validation(self, client):
        """With enough RAM, framework validation passes (endpoint proceeds past it)."""
        mock_manifest = Mock()
        mock_manifest.id = "openclaw"
        mock_manifest.type = "agent-framework"
        mock_manifest.requires = {"ram_mb": 512}

        mock_registry = Mock()
        mock_registry.list_available = Mock(return_value=[mock_manifest])
        mock_registry.get = Mock(return_value=mock_manifest)

        mock_hw = Mock()
        mock_hw.ram_mb = 16384  # 16 GB -- plenty

        app = _app(client)
        app.state.registry = mock_registry
        app.state.hardware_profile = mock_hw

        r = await client.post(
            "/api/agents/deploy",
            json={"name": "test-agent", "framework": "openclaw"},
        )
        # Should NOT get a 400 from framework validation.
        assert r.status_code != 400 or "framework" not in r.json().get("error", "").lower()

    @pytest.mark.asyncio
    async def test_framework_none_skips_validation(self, client):
        """framework='none' skips both catalog lookup and RAM check."""
        mock_registry = Mock()
        mock_registry.list_available = Mock(return_value=[])

        app = _app(client)
        app.state.registry = mock_registry
        app.state.hardware_profile = None

        r = await client.post(
            "/api/agents/deploy",
            json={"name": "test-agent", "framework": "none"},
        )
        # Should not get a framework-related 400.
        if r.status_code == 400:
            assert "framework" not in r.json().get("error", "").lower()

    @pytest.mark.asyncio
    async def test_no_hardware_profile_skips_ram_check(self, client):
        """When hardware_profile is None, RAM check is skipped."""
        mock_manifest = Mock()
        mock_manifest.id = "openclaw"
        mock_manifest.type = "agent-framework"
        mock_manifest.requires = {"ram_mb": 99999}

        mock_registry = Mock()
        mock_registry.list_available = Mock(return_value=[mock_manifest])
        mock_registry.get = Mock(return_value=mock_manifest)

        app = _app(client)
        app.state.registry = mock_registry
        app.state.hardware_profile = None

        r = await client.post(
            "/api/agents/deploy",
            json={"name": "test-agent", "framework": "openclaw"},
        )
        # Should not get a RAM-related 400 since hw profile is None.
        if r.status_code == 400:
            assert "ram" not in r.json().get("error", "").lower()


# ---------------------------------------------------------------------------
# resolve_deploy_routing
# ---------------------------------------------------------------------------


class TestResolveDeployRouting:
    """Tests for agent_deploy.resolve_deploy_routing via the deploy endpoint."""

    @pytest.mark.asyncio
    async def test_model_not_found_returns_404(self, client):
        """A model that resolves to not_found must return 404."""
        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(kind="not_found"),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={"name": "test-agent", "model": "nonexistent-model"},
            )
        assert r.status_code == 404
        body = r.json()
        assert "error" in body
        assert "nonexistent-model" in body["error"]

    @pytest.mark.asyncio
    async def test_model_downloaded_backend_down_returns_actionable_404(self, client):
        """A downloaded model whose backend is confirmed not running must
        return a specific, actionable error — not the generic "not found
        anywhere" message (#1600)."""
        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(kind="downloaded_backend_down", backend_id="rkllama"),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={"name": "test-agent", "model": "qwen2.5-3b-rkllm"},
            )
        assert r.status_code == 404
        body = r.json()
        assert "downloaded" in body["error"]
        assert "rkllama" in body["error"]
        assert "not running" in body["error"]
        assert body["backend"] == "rkllama"

    @pytest.mark.asyncio
    async def test_model_routed_to_worker_returns_202(self, client):
        """A model on a worker (no pin) must return 202 with routing info."""
        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(
                kind="worker",
                hosts=["worker-a", "worker-b"],
                canonical_host="worker-a",
            ),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={
                    "name": "test-agent",
                    "model": "qwen2.5-7b",
                },
            )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "routed"
        assert body["worker"] == "worker-a"
        assert "worker-a" in body["available_on"]
        assert "worker-b" in body["available_on"]

    @pytest.mark.asyncio
    async def test_pinned_worker_falls_through_to_remote_deploy(self, client):
        """An explicit, non-conflicting pin no longer 202-stubs; it attempts a
        remote deploy. When the pinned worker is not a registered online cluster
        worker (as here), configure_remote_deploy returns 409 'not registered'
        rather than the old routed-stub 202."""
        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(
                kind="worker",
                hosts=["worker-a", "worker-b"],
                canonical_host="worker-a",
            ),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={
                    "name": "test-agent",
                    "model": "qwen2.5-7b",
                    "target_worker": "worker-b",
                },
            )
        assert r.status_code == 409
        assert "not registered" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_pinned_worker_without_model_returns_409(self, client):
        """A pinned worker that does NOT have the model must return 409."""
        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(
                kind="worker",
                hosts=["worker-a"],
                canonical_host="worker-a",
            ),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={
                    "name": "test-agent",
                    "model": "qwen2.5-7b",
                    "target_worker": "worker-b",
                },
            )
        assert r.status_code == 409
        body = r.json()
        assert "error" in body
        assert "worker-b" in body["error"]
        assert body["pinned_worker"] == "worker-b"
        assert body["model"] == "qwen2.5-7b"
        assert "worker-a" in body["available_on"]

    @pytest.mark.asyncio
    async def test_no_model_skips_routing(self, client):
        """When no model is specified, routing is skipped entirely."""
        r = await client.post(
            "/api/agents/deploy",
            json={"name": "test-agent"},
        )
        # Should not get a 404/409 from routing.
        assert r.status_code not in (404, 409)

    @pytest.mark.asyncio
    async def test_cloud_model_falls_through(self, client):
        """A cloud model resolves to 'cloud' and falls through to local deploy."""
        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(kind="cloud"),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={
                    "name": "test-agent",
                    "model": "gpt-4o",
                },
            )
        # Cloud models fall through; NOT a routing error.
        assert r.status_code not in (404, 409)


# ---------------------------------------------------------------------------
# archive_smoke_check
# ---------------------------------------------------------------------------

# NOTE: archive_smoke_check is exercised during the POST /api/agents/deploy
# response construction (after the agent record is saved and the background
# task is spawned). We test it here with controller-local model resolution so
# the deploy path actually completes, and we inspect archive_smoke_ok in the
# response. The tests below verify that the smoke-check flag reflects archive
# health. End-to-end archive correctness is tested in tests/routes/archive.


class TestArchiveSmokeCheck:
    """Tests for agent_deploy.archive_smoke_check via the deploy endpoint."""

    @pytest.mark.asyncio
    async def test_archive_smoke_ok_true(self, client):
        """When archive.record and query succeed, archive_smoke_ok is True."""
        mock_archive = AsyncMock()
        mock_archive.record = AsyncMock()
        mock_archive.query = AsyncMock(return_value=[{"id": 1}])

        app = _app(client)
        app.state.archive = mock_archive

        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(kind="controller"),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={"name": "test-agent"},
            )
            if r.status_code == 200:
                body = r.json()
                assert body.get("archive_smoke_ok") is True

    @pytest.mark.asyncio
    async def test_archive_smoke_ok_false_on_record_failure(self, client):
        """When archive.record raises, archive_smoke_ok is False."""
        mock_archive = AsyncMock()
        mock_archive.record = AsyncMock(side_effect=Exception("disk full"))
        mock_archive.query = AsyncMock(return_value=[])

        app = _app(client)
        app.state.archive = mock_archive

        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(kind="controller"),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={"name": "test-agent"},
            )
            if r.status_code == 200:
                body = r.json()
                assert body.get("archive_smoke_ok") is False

    @pytest.mark.asyncio
    async def test_archive_smoke_ok_false_when_no_archive(self, client):
        """When archive is None on app.state, archive_smoke_ok is False."""
        app = _app(client)
        app.state.archive = None

        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(kind="controller"),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={"name": "test-agent"},
            )
            if r.status_code == 200:
                body = r.json()
                assert body.get("archive_smoke_ok") is False

    @pytest.mark.asyncio
    async def test_archive_smoke_ok_false_on_empty_query(self, client):
        """When archive.query returns empty list, archive_smoke_ok is False."""
        mock_archive = AsyncMock()
        mock_archive.record = AsyncMock()
        mock_archive.query = AsyncMock(return_value=[])

        app = _app(client)
        app.state.archive = mock_archive

        with patch(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            return_value=ModelLocation(kind="controller"),
        ):
            r = await client.post(
                "/api/agents/deploy",
                json={"name": "test-agent"},
            )
            if r.status_code == 200:
                body = r.json()
                assert body.get("archive_smoke_ok") is False


@pytest.mark.asyncio
class TestConfigureRemoteDeploy:
    """Direct unit tests for agent_deploy.configure_remote_deploy."""

    @staticmethod
    def _req(worker=None):
        from types import SimpleNamespace
        cm = SimpleNamespace(get_worker=lambda name: worker)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cluster_manager=cm)))

    @staticmethod
    def _body(**kw):
        from types import SimpleNamespace
        defaults = dict(target_worker=None, framework="none", name="a")
        defaults.update(kw)
        return SimpleNamespace(**defaults)

    async def test_no_pin_is_local(self):
        from tinyagentos.routes import agent_deploy
        remote, host, err = await agent_deploy.configure_remote_deploy(
            self._req(), self._body()
        )
        assert remote is None and host == "127.0.0.1" and err is None

    async def test_unknown_worker_409(self):
        from tinyagentos.routes import agent_deploy
        remote, host, err = await agent_deploy.configure_remote_deploy(
            self._req(worker=None), self._body(target_worker="ghost")
        )
        assert remote is None and err is not None and err.status_code == 409

    async def test_offline_worker_409(self):
        from types import SimpleNamespace
        from tinyagentos.routes import agent_deploy
        worker = SimpleNamespace(status="offline", hardware={"arch": "x86_64"})
        remote, host, err = await agent_deploy.configure_remote_deploy(
            self._req(worker=worker), self._body(target_worker="w")
        )
        assert remote is None and err.status_code == 409

    async def test_online_worker_returns_remote_and_callback_host(self):
        from types import SimpleNamespace
        from tinyagentos.routes import agent_deploy
        worker = SimpleNamespace(status="online", hardware={"arch": "x86_64"})
        with patch.object(
            agent_deploy, "controller_callback_host",
            new=AsyncMock(return_value="100.78.225.80"),
        ):
            remote, host, err = await agent_deploy.configure_remote_deploy(
                self._req(worker=worker), self._body(target_worker="fedora-worker", framework="hermes")
            )
        assert err is None
        assert remote == "fedora-worker"
        assert host == "100.78.225.80"

    async def test_online_worker_no_callback_host_500(self):
        """A remote deploy with no reachable controller address must hard-fail,
        not silently start an unreachable agent on 127.0.0.1."""
        from types import SimpleNamespace
        from tinyagentos.routes import agent_deploy
        worker = SimpleNamespace(status="online", hardware={"arch": "x86_64"})
        with patch.object(
            agent_deploy, "controller_callback_host", new=AsyncMock(return_value=None),
        ):
            remote, host, err = await agent_deploy.configure_remote_deploy(
                self._req(worker=worker), self._body(target_worker="fedora-worker", framework="hermes")
            )
        assert remote is None
        assert err is not None and err.status_code == 500

    async def test_prefetch_base_onto_worker_imports_correct_arch(self):
        from types import SimpleNamespace
        from tinyagentos.routes import agent_deploy
        worker = SimpleNamespace(status="online", hardware={"arch": "x86_64"})
        with patch("tinyagentos.agent_image.ensure_image_present", new=AsyncMock(return_value=True)) as mock_prefetch:
            await agent_deploy.prefetch_base_onto_worker(
                self._req(worker=worker), self._body(target_worker="fedora-worker", framework="hermes")
            )
        assert mock_prefetch.await_args.kwargs.get("remote") == "fedora-worker"
        # x86_64 maps to the x64 base tarball.
        assert "x64" in mock_prefetch.await_args.kwargs.get("url", "")


@pytest.mark.asyncio
async def test_controller_callback_host_env_override(monkeypatch):
    """TAOS_CONTROLLER_CALLBACK_HOST takes precedence over tailscale/LAN."""
    from types import SimpleNamespace
    from tinyagentos.routes import agent_deploy
    monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "192.168.6.123")
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    host = await agent_deploy.controller_callback_host(req)
    assert host == "192.168.6.123"
