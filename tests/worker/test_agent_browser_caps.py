"""Tests for WorkerAgent extra_capabilities + advertise_url (browser-worker additions)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.worker.agent import WorkerAgent


class TestWorkerAgentBrowserCaps:
    def test_extra_capabilities_stored(self):
        agent = WorkerAgent(
            "http://controller:6969",
            extra_capabilities=["browser"],
            advertise_url="http://10.0.0.5:7080",
        )
        assert "browser" in agent.extra_capabilities

    def test_advertise_url_stored(self):
        agent = WorkerAgent(
            "http://controller:6969",
            extra_capabilities=["browser"],
            advertise_url="http://10.0.0.5:7080",
        )
        assert agent.advertise_url == "http://10.0.0.5:7080"

    def test_no_extra_capabilities_defaults_to_empty(self):
        agent = WorkerAgent("http://controller:6969")
        assert agent.extra_capabilities == []

    def test_no_advertise_url_defaults_to_none(self):
        agent = WorkerAgent("http://controller:6969")
        assert agent.advertise_url is None

    def test_extra_capabilities_passed_as_none_is_empty(self):
        agent = WorkerAgent("http://controller:6969", extra_capabilities=None)
        assert agent.extra_capabilities == []


@pytest.mark.asyncio
class TestRegisterWithBrowserCaps:
    """Verify register() forwards browser capability + pinned URL to controller."""

    async def test_register_includes_browser_capability(self):
        agent = WorkerAgent(
            "http://controller:6969",
            name="browser-node",
            extra_capabilities=["browser"],
            advertise_url="http://10.0.0.5:7080",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        captured: list[dict] = []

        async def _mock_post(url, json=None, **kwargs):
            if json is not None:
                captured.append(json)
            return mock_response

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("not running"))
            mock_client.post = AsyncMock(side_effect=_mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                result = await agent.register()

        assert result is True
        assert len(captured) == 1
        payload = captured[0]
        assert "browser" in payload["capabilities"]

    async def test_register_uses_advertise_url(self):
        agent = WorkerAgent(
            "http://controller:6969",
            name="browser-node",
            extra_capabilities=["browser"],
            advertise_url="http://10.0.0.5:7080",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        captured: list[dict] = []

        async def _mock_post(url, json=None, **kwargs):
            if json is not None:
                captured.append(json)
            return mock_response

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("not running"))
            mock_client.post = AsyncMock(side_effect=_mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                await agent.register()

        assert captured[0]["url"] == "http://10.0.0.5:7080"

    async def test_register_without_advertise_url_falls_back(self):
        """Without advertise_url, register() falls back to get_worker_url()."""
        agent = WorkerAgent(
            "http://controller:6969",
            name="plain-node",
            worker_port=9999,
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        captured: list[dict] = []

        async def _mock_post(url, json=None, **kwargs):
            if json is not None:
                captured.append(json)
            return mock_response

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("not running"))
            mock_client.post = AsyncMock(side_effect=_mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                await agent.register()

        # Should contain :9999 in the URL, not "http://10.0.0.5:7080"
        assert ":9999" in captured[0]["url"]

    async def test_extra_caps_merged_with_backend_caps(self):
        """extra_capabilities are unioned with detected backend caps."""
        agent = WorkerAgent(
            "http://controller:6969",
            extra_capabilities=["browser"],
            advertise_url="http://10.0.0.5:7080",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        captured: list[dict] = []

        async def _mock_post(url, json=None, **kwargs):
            if json is not None:
                captured.append(json)
            return mock_response

        fake_backend = {
            "name": "ollama@http://localhost:11434",
            "type": "ollama",
            "url": "http://localhost:11434",
            "capabilities": ["llm-chat", "embedding"],
            "models": [],
            "loaded_models": [],
            "status": "ok",
            "kv_quant_support": {"k": ["fp16"], "v": ["fp16"], "boundary": False},
        }

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=_mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[fake_backend]):
                with patch("shutil.which", return_value=None):
                    await agent.register()

        caps = captured[0]["capabilities"]
        assert "browser" in caps
        assert "llm-chat" in caps
        assert "embedding" in caps

    async def test_heartbeat_includes_browser_capability(self):
        """heartbeat() unions extra_capabilities into the posted caps too."""
        agent = WorkerAgent(
            "http://controller:6969",
            name="browser-node",
            extra_capabilities=["browser"],
            advertise_url="http://10.0.0.5:7080",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200

        captured: list[dict] = []

        async def _mock_post(url, json=None, **kwargs):
            if json is not None:
                captured.append(json)
            return mock_response

        snap = {
            "storage_cap_bytes": 0,
            "storage_used_bytes": 0,
            "bytes_deduped_total": 0,
        }

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=_mock_post)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                with patch(
                    "tinyagentos.cluster.worker_capacity.capacity_snapshot",
                    return_value=snap,
                ):
                    status = await agent.heartbeat()

        assert status == 200
        assert len(captured) == 1
        assert "browser" in captured[0]["capabilities"]
