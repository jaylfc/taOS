"""Tests for OllamaInstaller — Ollama daemon model puller.

Mocks httpx at the boundary so tests don't actually hit a daemon.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class _MockStreamCtx:
    """Mock async context manager for httpx's client.stream() return value.

    httpx's stream() returns a context manager directly (not a coroutine).
    AsyncMock can't be used here because Python's `async with` doesn't await
    the call itself, only the __aenter__/__aexit__ methods.
    """
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *_args):
        return None


def _make_pull_response(events):
    """Build a mock streaming response that yields the given JSON events."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()

    async def _aiter_lines():
        for line in events:
            yield line

    resp.aiter_lines = _aiter_lines
    return resp


class TestDefaultHostResolution:
    def test_default_no_env(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        from tinyagentos.installers.ollama_installer import _default_host
        assert _default_host() == "http://localhost:11434"

    def test_explicit_url_from_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.5:11434")
        from tinyagentos.installers.ollama_installer import _default_host
        assert _default_host() == "http://10.0.0.5:11434"

    def test_bare_host_port_gets_scheme(self, monkeypatch):
        """OLLAMA_HOST sometimes has no scheme; we add http:// rather than failing."""
        monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:11434")
        from tinyagentos.installers.ollama_installer import _default_host
        assert _default_host() == "http://10.0.0.5:11434"

    def test_explicit_host_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://from-env:11434")
        from tinyagentos.installers.ollama_installer import OllamaInstaller
        i = OllamaInstaller(host="http://explicit:11434")
        assert i.host == "http://explicit:11434"


class TestModelNameResolution:
    """variant.ollama_name overrides app_id; falls back to app_id if missing."""

    @pytest.mark.asyncio
    async def test_uses_variant_ollama_name_when_set(self):
        from tinyagentos.installers.ollama_installer import OllamaInstaller

        i = OllamaInstaller(host="http://localhost:11434")

        with patch("httpx.AsyncClient") as mock_client_class:
            client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = client
            client.get.return_value = MagicMock(raise_for_status=MagicMock(), status_code=200)

            stream_resp = _make_pull_response([
                '{"status": "downloading"}',
                '{"status": "success"}',
            ])
            client.stream = MagicMock(return_value=_MockStreamCtx(stream_resp))

            result = await i.install(
                "mymodel",
                install_config={},
                variant={"ollama_name": "qwen2.5:3b"},
            )

        assert result["success"] is True
        assert client.stream.call_args.kwargs["json"]["name"] == "qwen2.5:3b"

    @pytest.mark.asyncio
    async def test_falls_back_to_app_id(self):
        from tinyagentos.installers.ollama_installer import OllamaInstaller

        i = OllamaInstaller(host="http://localhost:11434")

        with patch("httpx.AsyncClient") as mock_client_class:
            client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = client
            client.get.return_value = MagicMock(raise_for_status=MagicMock(), status_code=200)

            stream_resp = _make_pull_response(['{"status": "success"}'])
            client.stream = MagicMock(return_value=_MockStreamCtx(stream_resp))

            result = await i.install("qwen2.5:3b", install_config={}, variant=None)

        assert result["success"] is True
        assert client.stream.call_args.kwargs["json"]["name"] == "qwen2.5:3b"


class TestDaemonNotReachable:
    @pytest.mark.asyncio
    async def test_returns_helpful_error_when_daemon_down(self):
        from tinyagentos.installers.ollama_installer import OllamaInstaller

        i = OllamaInstaller(host="http://localhost:11434")

        with patch("httpx.AsyncClient") as mock_client_class:
            client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = client
            client.get.side_effect = httpx.ConnectError("connection refused")

            result = await i.install("qwen2.5:3b", install_config={}, variant=None)

        assert result["success"] is False
        assert "not reachable" in result["error"].lower()
        assert "install-ollama.sh" in result["error"]


class TestPullErrorPropagation:
    @pytest.mark.asyncio
    async def test_pull_error_event_returns_failure(self):
        from tinyagentos.installers.ollama_installer import OllamaInstaller

        i = OllamaInstaller(host="http://localhost:11434")

        with patch("httpx.AsyncClient") as mock_client_class:
            client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = client
            client.get.return_value = MagicMock(raise_for_status=MagicMock(), status_code=200)

            stream_resp = _make_pull_response([
                '{"status": "downloading"}',
                '{"error": "model not found in library"}',
            ])
            client.stream = MagicMock(return_value=_MockStreamCtx(stream_resp))

            result = await i.install("nope", install_config={}, variant=None)

        assert result["success"] is False
        assert "model not found" in result["error"]

    @pytest.mark.asyncio
    async def test_no_success_event_returns_failure(self):
        """If the stream ends without a {status: success} event, the pull
        didn't actually finish — we shouldn't claim success."""
        from tinyagentos.installers.ollama_installer import OllamaInstaller

        i = OllamaInstaller(host="http://localhost:11434")

        with patch("httpx.AsyncClient") as mock_client_class:
            client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = client
            client.get.return_value = MagicMock(raise_for_status=MagicMock(), status_code=200)

            stream_resp = _make_pull_response([
                '{"status": "downloading"}',
                '{"status": "verifying"}',
            ])
            client.stream = MagicMock(return_value=_MockStreamCtx(stream_resp))

            result = await i.install("incomplete", install_config={}, variant=None)

        assert result["success"] is False
        assert "verifying" in result["error"]


class TestGetInstallerWiring:
    def test_get_installer_returns_ollama_installer(self):
        from tinyagentos.installers.base import get_installer
        from tinyagentos.installers.ollama_installer import OllamaInstaller

        i = get_installer("ollama")
        assert isinstance(i, OllamaInstaller)
