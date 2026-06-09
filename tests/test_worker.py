"""Tests for tinyagentos.worker.agent — WorkerAgent logic."""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tinyagentos.worker.agent import WorkerAgent


class TestDetectCapabilities:
    """Test capability detection from backend lists."""

    def test_empty_backends(self):
        agent = WorkerAgent("http://localhost:6969")
        with patch("shutil.which", return_value=None):
            assert agent.detect_capabilities([]) == []

    def test_ollama_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "ollama", "url": "http://localhost:11434"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "embedding" in caps
        # Ollama only advertises llm-chat + embedding in BACKEND_CAPABILITIES
        assert "image-generation" not in caps

    def test_rkllama_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "rkllama", "url": "http://localhost:8080"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "embedding" in caps
        assert "reranking" in caps

    def test_llama_cpp_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "llama-cpp", "url": "http://localhost:8080"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "embedding" in caps
        assert "image-generation" not in caps
        assert "reranking" not in caps

    def test_vllm_backend(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "vllm", "url": "http://localhost:8000"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "llm-chat" in caps
        assert "image-generation" not in caps

    def test_sd_cpp_backend(self):
        """A sd-cpp backend advertises image-generation only."""
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "sd-cpp", "url": "http://localhost:7864"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "image-generation" in caps
        assert "llm-chat" not in caps

    def test_sd_cpp_backend(self):
        """An sd-cpp backend advertises image-generation only."""
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "sd-cpp", "url": "http://localhost:7864"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert "image-generation" in caps
        assert "embedding" not in caps

    def test_respects_preattached_capabilities(self):
        """If the caller attaches a per-backend capabilities field (modern
        detect_backends shape from live probing), detect_capabilities uses
        it verbatim without consulting BACKEND_CAPABILITIES."""
        agent = WorkerAgent("http://localhost:6969")
        backends = [
            {"type": "rkllama", "url": "http://x", "capabilities": ["embedding"]},
        ]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert caps == ["embedding"]

    def test_multiple_backends_deduplicates(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [
            {"type": "ollama", "url": "http://localhost:11434"},
            {"type": "rkllama", "url": "http://localhost:8080"},
        ]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        # Should be sorted and deduplicated
        assert caps == sorted(set(caps))
        assert "reranking" in caps

    def test_capabilities_are_sorted(self):
        agent = WorkerAgent("http://localhost:6969")
        backends = [{"type": "rkllama", "url": "http://localhost:8080"}]
        with patch("shutil.which", return_value=None):
            caps = agent.detect_capabilities(backends)
        assert caps == sorted(caps)


class TestWorkerAgent:
    """Test WorkerAgent initialization and URL generation."""

    def test_default_name_is_hostname(self):
        import socket
        agent = WorkerAgent("http://localhost:6969")
        assert agent.name == socket.gethostname()

    def test_custom_name(self):
        agent = WorkerAgent("http://localhost:6969", name="gpu-box")
        assert agent.name == "gpu-box"

    def test_controller_url_strips_trailing_slash(self):
        agent = WorkerAgent("http://localhost:6969/")
        assert agent.controller_url == "http://localhost:6969"

    def test_get_worker_url_with_port(self):
        agent = WorkerAgent("http://localhost:6969", worker_port=9999)
        url = agent.get_worker_url()
        assert ":9999" in url
        assert url.startswith("http://")

    def test_get_worker_url_without_port(self):
        agent = WorkerAgent("http://localhost:6969", worker_port=0)
        url = agent.get_worker_url()
        assert url.startswith("http://")
        # Should not end with :0
        assert ":0" not in url


@pytest.mark.asyncio
class TestRegistration:
    """Test worker registration with mocked HTTP."""

    async def test_register_success(self):
        agent = WorkerAgent("http://controller:6969", name="test-worker")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            # detect_backends GET calls return failures (no backends running)
            mock_client.get = AsyncMock(side_effect=Exception("not running"))
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                result = await agent.register()

        assert result is True
        assert agent._registered is True

    async def test_register_failure(self):
        agent = WorkerAgent("http://controller:6969", name="test-worker")

        with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
            with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
                mock_client_cls.return_value = mock_client

                result = await agent.register()

        assert result is False
        assert agent._registered is False


@pytest.mark.asyncio
class TestHeartbeat:
    """Test heartbeat sending with mocked HTTP."""

    async def test_heartbeat_success(self):
        agent = WorkerAgent("http://controller:6969", name="test-worker")
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=42.0):
                result = await agent.heartbeat()

        # heartbeat() returns the int HTTP status code (or 0 on failure), see #166
        assert result == 200

    async def test_heartbeat_failure(self):
        agent = WorkerAgent("http://controller:6969", name="test-worker")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=Exception("timeout"))
            mock_client_cls.return_value = mock_client

            with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=0.0):
                result = await agent.heartbeat()

        # On transport error heartbeat() returns 0, not False
        assert result == 0


@pytest.mark.asyncio
class TestDetectBackends:
    """Test backend discovery with mocked HTTP."""

    async def test_no_backends_running(self):
        agent = WorkerAgent("http://localhost:6969")

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client

            backends = await agent.detect_backends()

        assert backends == []

    async def test_ollama_running(self):
        agent = WorkerAgent("http://localhost:6969")
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            async def mock_get(url):
                if "11434" in url:
                    return mock_response
                raise Exception("not running")

            mock_client.get = AsyncMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            backends = await agent.detect_backends()

        assert len(backends) == 1
        assert backends[0]["type"] == "ollama"
        assert backends[0]["url"] == "http://localhost:11434"
