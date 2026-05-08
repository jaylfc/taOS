import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from tinyagentos.backend_adapters import (
    check_backend_health, RkLlamaAdapter, OllamaAdapter, LlamaCppAdapter, VllmAdapter, get_adapter,
    CloudAPIAdapter,
)

class TestGetAdapter:
    def test_returns_rkllama(self):
        assert isinstance(get_adapter("rkllama"), RkLlamaAdapter)
    def test_returns_ollama(self):
        assert isinstance(get_adapter("ollama"), OllamaAdapter)
    def test_returns_llama_cpp(self):
        assert isinstance(get_adapter("llama-cpp"), LlamaCppAdapter)
    def test_returns_vllm(self):
        assert isinstance(get_adapter("vllm"), VllmAdapter)
    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown backend type"):
            get_adapter("unknown")


class TestCheckBackendHealthResilience:
    """A misconfigured backend (unknown type, raising adapter) must not
    take the whole /api/backends endpoint with it. check_backend_health
    must always return a structured dict.
    """

    @pytest.mark.asyncio
    async def test_unknown_type_returns_unsupported_envelope(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        backend = {"name": "local-mlc-llm", "type": "llm-runtime", "url": "http://x"}
        result = await check_backend_health(client, backend)
        assert result["healthy"] is False
        assert result["status"] == "unsupported"
        assert "Unknown backend type" in result["error"]
        assert result["name"] == "local-mlc-llm"
        assert result["type"] == "llm-runtime"
        assert result["models"] == []

    @pytest.mark.asyncio
    async def test_adapter_exception_returns_error_envelope(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        # Use a real type whose adapter we'll make raise — patch the
        # registered adapter's health method.
        from tinyagentos.backend_adapters import _ADAPTERS
        original = _ADAPTERS["ollama"].health
        async def boom(*_a, **_kw):
            raise RuntimeError("network on fire")
        _ADAPTERS["ollama"].health = boom  # type: ignore[method-assign]
        try:
            result = await check_backend_health(client, {"name": "x", "type": "ollama", "url": "http://x"})
        finally:
            _ADAPTERS["ollama"].health = original  # type: ignore[method-assign]
        assert result["healthy"] is False
        assert result["status"] == "error"
        assert "network on fire" in result["error"]
        assert result["models"] == []

    @pytest.mark.asyncio
    async def test_missing_name_does_not_crash(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        result = await check_backend_health(client, {"type": "totally-bogus", "url": "http://x"})
        assert result["healthy"] is False
        assert result["name"] == ""

class TestRkLlamaAdapter:
    @pytest.mark.asyncio
    async def test_parse_health_response(self):
        adapter = RkLlamaAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        tags_response = MagicMock()
        tags_response.status_code = 200
        tags_response.raise_for_status = MagicMock(return_value=None)
        tags_response.json.return_value = {
            "models": [
                {"name": "qwen3-embedding-0.6b", "size": 892000000},
                {"name": "qwen3-reranker-0.6b", "size": 892000000},
            ]
        }
        mock_client.get.return_value = tags_response
        result = await adapter.health(mock_client, "http://localhost:8080")
        assert result["status"] == "ok"
        assert len(result["models"]) == 2
        assert result["models"][0]["name"] == "qwen3-embedding-0.6b"
        assert "response_ms" in result

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        adapter = RkLlamaAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        result = await adapter.health(mock_client, "http://localhost:8080")
        assert result["status"] == "error"
        assert result["models"] == []

class TestOllamaAdapter:
    @pytest.mark.asyncio
    async def test_parse_tags_response(self):
        adapter = OllamaAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        tags_response = MagicMock()
        tags_response.status_code = 200
        tags_response.raise_for_status = MagicMock(return_value=None)
        tags_response.json.return_value = {"models": [{"name": "llama3:latest", "size": 4700000000}]}
        mock_client.get.return_value = tags_response
        result = await adapter.health(mock_client, "http://localhost:11434")
        assert result["status"] == "ok"
        assert len(result["models"]) == 1

class TestCloudAPIAdapter:
    @pytest.mark.asyncio
    async def test_200_is_ok(self):
        adapter = CloudAPIAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}
        mock_client.get.return_value = resp
        result = await adapter.health(mock_client, "https://api.openai.com/v1")
        mock_client.get.assert_called_once_with("https://api.openai.com/v1/models", timeout=10)
        assert result["status"] == "ok"
        assert result["models"] == [{"name": "gpt-4o", "size_mb": 0}, {"name": "gpt-4o-mini", "size_mb": 0}]
        assert "response_ms" in result

    @pytest.mark.asyncio
    async def test_401_is_ok(self):
        """401 = server is reachable, just needs auth — count as online."""
        adapter = CloudAPIAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.status_code = 401
        mock_client.get.return_value = resp
        result = await adapter.health(mock_client, "https://api.openai.com/v1")
        assert result["status"] == "ok"
        assert result["models"] == []

    @pytest.mark.asyncio
    async def test_403_is_ok(self):
        adapter = CloudAPIAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.status_code = 403
        mock_client.get.return_value = resp
        result = await adapter.health(mock_client, "https://api.openai.com/v1")
        assert result["status"] == "ok"
        assert result["models"] == []

    @pytest.mark.asyncio
    async def test_500_is_error(self):
        adapter = CloudAPIAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.status_code = 500
        mock_client.get.return_value = resp
        result = await adapter.health(mock_client, "https://api.openai.com/v1")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_connection_error_is_error(self):
        adapter = CloudAPIAdapter()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        result = await adapter.health(mock_client, "https://api.openai.com/v1")
        mock_client.get.assert_called_once_with("https://api.openai.com/v1/models", timeout=10)
        assert result["status"] == "error"
        assert result["models"] == []

    def test_get_adapter_openai_uses_cloud(self):
        assert isinstance(get_adapter("openai"), CloudAPIAdapter)

    def test_get_adapter_anthropic_uses_cloud(self):
        assert isinstance(get_adapter("anthropic"), CloudAPIAdapter)

    def test_get_adapter_openrouter(self):
        assert isinstance(get_adapter("openrouter"), CloudAPIAdapter)

    def test_get_adapter_kilocode(self):
        assert isinstance(get_adapter("kilocode"), CloudAPIAdapter)
