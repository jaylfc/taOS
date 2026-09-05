import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx
from tinyagentos.qmd_client import QmdClient


def _make_response(json_data, status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


class TestQmdClient:
    @pytest.mark.asyncio
    async def test_embed_returns_vector(self):
        client = QmdClient("http://localhost:7832")
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = _make_response({"embedding": [0.1, 0.2, 0.3]})
        client._client = mock_http

        vector = await client.embed("test query")
        assert vector == [0.1, 0.2, 0.3]
        mock_http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_ok(self):
        client = QmdClient("http://localhost:7832")
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_response({"status": "ok"})
        client._client = mock_http

        result = await client.health()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_unreachable(self):
        client = QmdClient("http://localhost:7832")
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.side_effect = httpx.ConnectError("Connection refused")
        client._client = mock_http

        result = await client.health()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_embed_timeout_default(self):
        """Unset timeout should pass USE_CLIENT_DEFAULT to httpx, preserving
        the 60 s client default."""
        client = QmdClient("http://localhost:7832")
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = _make_response({"embedding": [0.1, 0.2]})
        client._client = mock_http

        await client.embed("test")
        call_kwargs = mock_http.post.call_args.kwargs
        assert call_kwargs["timeout"] is httpx.USE_CLIENT_DEFAULT

    @pytest.mark.asyncio
    async def test_embed_timeout_override(self):
        """Explicit timeout should be passed through to httpx."""
        client = QmdClient("http://localhost:7832")
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = _make_response({"embedding": [0.1, 0.2]})
        client._client = mock_http

        await client.embed("test", timeout=30.0)
        call_kwargs = mock_http.post.call_args.kwargs
        assert call_kwargs["timeout"] == 30.0
