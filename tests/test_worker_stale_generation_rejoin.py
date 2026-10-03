"""Tests for worker stale generation rejoin fix (taOS #...).

Covers the fix where a worker holding a stale cluster generation can rejoin
the controller by adopting the echoed generation from a 409 stale_generation
response, monotonically (only if the echoed generation is higher).
"""
from __future__ import annotations
import json
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tinyagentos.worker.agent import WorkerAgent, _NEEDS_REPAIR
from tinyagentos.worker.pairing import save_signing_key
from tinyagentos.routes.cluster import register_worker
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo
from fastapi import Request
from starlette.datastructures import Headers


class TestWorkerStaleGenerationRejoin:
    """WorkerAgent-level tests for stale generation rejoin."""

    @pytest.fixture
    def agent_with_key(self, tmp_path):
        """Create a WorkerAgent with a valid signing key."""
        save_signing_key(tmp_path, secrets.token_bytes(32))
        agent = WorkerAgent("http://controller:6969", name="test-worker", state_dir=tmp_path)
        return agent

    @pytest.mark.asyncio
    async def test_worker_rejoins_after_stale_generation_409(self, agent_with_key):
        """Worker with stale generation: heartbeat 404 -> register 409 with
        echoed HIGHER generation -> second register succeeds carrying the
        refreshed generation.
        """
        agent = agent_with_key
        agent._generation = 5  # Stale generation (controller is at 7)

        # Track the generation sent in each register call
        sent_generations = []

        # Sequence of responses:
        # 1. heartbeat -> 404 (controller forgot worker)
        # 2. register -> 409 stale_generation with generation=7
        # 3. register -> 200 with generation=7
        call_count = {"register": 0, "heartbeat": 0}

        async def mock_heartbeat(**kwargs):
            call_count["heartbeat"] += 1
            if call_count["heartbeat"] == 1:
                return 404  # Controller forgot us
            return 200

        async def mock_register():
            call_count["register"] += 1
            if call_count["register"] == 1:
                # First register: 409 stale_generation with higher generation
                mock_resp = MagicMock()
                mock_resp.status_code = 409
                mock_resp.json.return_value = {"error": "stale_generation", "generation": 7}
                mock_resp.raise_for_status.side_effect = Exception("409 Conflict")
                return mock_resp
            elif call_count["register"] == 2:
                # Second register: 200 OK with generation=7
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = {"status": "registered", "generation": 7}
                return mock_resp
            # Should not reach here
            raise AssertionError("Too many register calls")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            # heartbeat uses client.post, register uses client.post
            async def mock_post(url, content, headers):
                if "/heartbeat" in url:
                    status = await mock_heartbeat()
                    resp = MagicMock()
                    resp.status_code = status
                    if status == 200:
                        resp.json.return_value = {"status": "ok", "generation": 7}
                    return resp
                elif "/workers" in url:
                    resp = await mock_register()
                    # Capture the generation sent in the request
                    body = json.loads(content)
                    sent_generations.append(body.get("generation"))
                    return resp
                raise AssertionError(f"Unexpected URL: {url}")

            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=0.0):
                    # First heartbeat -> 404
                    hb_status = await agent.heartbeat()
                    assert hb_status == 404
                    assert agent._registered is False  # run loop would drop _registered

                    # First register -> 409 stale_generation with generation=7
                    reg_result = await agent.register()
                    assert reg_result is False  # 409 -> False
                    # Worker should have adopted generation 7
                    assert agent._generation == 7, f"Expected generation 7, got {agent._generation}"

                    # Second register -> 200 with generation=7
                    reg_result2 = await agent.register()
                    assert reg_result2 is True
                    assert agent._generation == 7

        # Verify the generations sent: first register sent stale (5), second sent fresh (7)
        assert sent_generations[0] == 5, "First register should send stale generation"
        assert sent_generations[1] == 7, "Second register should send adopted generation"

    @pytest.mark.asyncio
    async def test_worker_ignores_lower_generation_echo(self, agent_with_key):
        """CONTROL: 409 echoing a LOWER generation is NOT adopted."""
        agent = agent_with_key
        agent._generation = 10  # Worker has higher generation

        call_count = {"register": 0}

        async def mock_register():
            call_count["register"] += 1
            if call_count["register"] == 1:
                # 409 stale_generation with LOWER generation
                mock_resp = MagicMock()
                mock_resp.status_code = 409
                mock_resp.json.return_value = {"error": "stale_generation", "generation": 5}
                mock_resp.raise_for_status.side_effect = Exception("409 Conflict")
                return mock_resp
            raise AssertionError("Too many register calls")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_post(url, content, headers):
                if "/workers" in url:
                    return await mock_register()
                raise AssertionError(f"Unexpected URL: {url}")

            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                reg_result = await agent.register()
                assert reg_result is False
                # Generation should NOT be downgraded
                assert agent._generation == 10, f"Generation should not be downgraded, got {agent._generation}"

    @pytest.mark.asyncio
    async def test_worker_ignores_host_lan_ip_conflict_409(self, agent_with_key):
        """CONTROL: host_lan_ip conflict 409 does not touch _generation."""
        agent = agent_with_key
        agent._generation = 7

        call_count = {"register": 0}

        async def mock_register():
            call_count["register"] += 1
            if call_count["register"] == 1:
                # 409 host_lan_ip conflict (no generation in body)
                mock_resp = MagicMock()
                mock_resp.status_code = 409
                mock_resp.json.return_value = {
                    "error": "Worker 'other' already registered for host 10.0.0.1; only one worker LXC per host is supported"
                }
                mock_resp.raise_for_status.side_effect = Exception("409 Conflict")
                return mock_resp
            raise AssertionError("Too many register calls")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_post(url, content, headers):
                if "/workers" in url:
                    return await mock_register()
                raise AssertionError(f"Unexpected URL: {url}")

            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                reg_result = await agent.register()
                assert reg_result is False
                # Generation should be untouched
                assert agent._generation == 7, f"Generation should be untouched, got {agent._generation}"


class TestRouteStaleGeneration409:
    """Route-level tests for the register_worker endpoint."""

    @pytest.fixture
    def cluster_manager(self):
        """Create a ClusterManager with a known generation."""
        mgr = ClusterManager()
        # Manually set generation to simulate controller state
        mgr._generation = 7
        return mgr

    @pytest.fixture
    def mock_request(self, cluster_manager):
        """Create a mock FastAPI request with cluster_manager in app.state."""
        from types import SimpleNamespace
        app = SimpleNamespace()
        app.state = SimpleNamespace()
        app.state.cluster_manager = cluster_manager
        app.state.cluster_pairing = None

        request = MagicMock(spec=Request)
        request.app = app
        request.state = SimpleNamespace()
        request.state.hmac_worker_name = "test-worker"
        return request

    @pytest.mark.asyncio
    async def test_route_stale_generation_409_echoes_generation(self, mock_request):
        """Route-level: stale_generation 409 must echo the controller's generation."""
        body = WorkerRegister(
            name="test-worker",
            url="http://10.0.0.1:9000",
            hardware={"cpu": "x86", "ram_mb": 8192},
            generation=5,  # Stale generation
        )

        # Mock require_worker_hmac to pass
        with patch("tinyagentos.routes.cluster.require_worker_hmac", new=AsyncMock()):
            response = await register_worker(mock_request, body)

        assert response.status_code == 409
        body_data = response.body.decode() if isinstance(response.body, bytes) else response.body
        data = json.loads(body_data)
        assert data["error"] == "stale_generation"
        assert "generation" in data, "Response must include generation"
        assert data["generation"] == 7, f"Expected generation 7, got {data['generation']}"

    @pytest.mark.asyncio
    async def test_route_host_lan_ip_conflict_409_no_generation(self, mock_request, cluster_manager):
        """Route-level: host_lan_ip conflict 409 must NOT include generation."""
        # Pre-register a worker with the same host_lan_ip
        existing = WorkerInfo(
            name="other-worker",
            url="http://10.0.0.2:9000",
            host_lan_ip="10.0.0.1",
        )
        await cluster_manager.register_worker(existing)

        body = WorkerRegister(
            name="test-worker",
            url="http://10.0.0.1:9000",
            hardware={"cpu": "x86", "ram_mb": 8192},
            host_lan_ip="10.0.0.1",  # Conflicts with existing
            generation=7,
        )

        with patch("tinyagentos.routes.cluster.require_worker_hmac", new=AsyncMock()):
            response = await register_worker(mock_request, body)

        assert response.status_code == 409
        body_data = response.body.decode() if isinstance(response.body, bytes) else response.body
        data = json.loads(body_data)
        assert "host" in data["error"]
        assert "generation" not in data, "host_lan_ip conflict 409 must not include generation"


# Need to import WorkerRegister for the route tests
from tinyagentos.routes.cluster import WorkerRegister