"""Tests for taOSmd client wrapper and answer_context integration."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.taosmd_client import (
    AnswerContextResult,
    TaOSmdClient,
    calculate_agent_budget,
    get_taosmd_client,
)


class TestTaOSmdClient:
    """Tests for TaOSmdClient."""

    @pytest.fixture
    def mock_http_client(self):
        """Mock httpx client."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def taosmd_client(self, mock_http_client):
        """Create TaOSmdClient with mocked HTTP client."""
        return TaOSmdClient(http_client=mock_http_client, base_url="http://localhost:7900", use_in_process=False)

    @pytest.mark.asyncio
    async def test_answer_context_http_success(self, taosmd_client, mock_http_client):
        """Test successful answer_context call via HTTP.

        reasoning_reserve must NOT be sent over HTTP (the server rejects it).
        """
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "context": "Assembled context for agent",
            "dropped": 2,
            "layers": {"l0": 50, "l1": 100, "l2": 200},
            "total_tokens": 350,
            "intent": "question",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_resp)

        result = await taosmd_client.answer_context(
            query="What is the weather?",
            agent="test-agent",
            budget_tokens=1000,
            now=datetime.now(timezone.utc),
        )

        assert isinstance(result, AnswerContextResult)
        assert result.context == "Assembled context for agent"
        assert result.dropped == 2
        assert result.layers == {"l0": 50, "l1": 100, "l2": 200}
        assert result.total_tokens == 350
        assert result.intent == "question"
        assert result.latency_ms >= 0

        mock_http_client.post.assert_awaited_once()
        call_args = mock_http_client.post.call_args
        assert call_args[0][0] == "http://localhost:7900/answer_context"
        payload = call_args[1]["json"]
        assert payload["question"] == "What is the weather?"
        assert payload["agent"] == "test-agent"
        assert payload["budget_tokens"] == 1000
        # reasoning_reserve must not be sent to the server
        assert "reasoning_reserve" not in payload

    @pytest.mark.asyncio
    async def test_answer_context_http_failure_fallback(self, taosmd_client, mock_http_client):
        """Test answer_context degrades to empty context on total failure (logged, not silent)."""
        mock_http_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        result = await taosmd_client.answer_context(
            query="What is the weather?",
            agent="test-agent",
            budget_tokens=1000,
            now=datetime.now(timezone.utc),
        )

        assert isinstance(result, AnswerContextResult)
        assert result.context == ""
        assert result.dropped == 0
        assert result.layers == {}
        assert result.total_tokens == 0
        assert result.intent == "unknown"
        assert result.latency_ms == 0.0

    @pytest.mark.asyncio
    async def test_search_memory_http_success(self, taosmd_client, mock_http_client):
        """Test search_memory via HTTP."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "hits": [
                {"id": "1", "text": "Memory 1", "metadata": {}},
                {"id": "2", "text": "Memory 2", "metadata": {}},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        results = await taosmd_client.search_memory(
            query="test query",
            agent="test-agent",
            limit=10,
            collection="workspace",
        )

        assert len(results) == 2
        assert results[0]["id"] == "1"
        assert results[1]["text"] == "Memory 2"

        mock_http_client.get.assert_awaited_once()
        call_args = mock_http_client.get.call_args
        assert call_args[0][0] == "http://localhost:7900/search"
        params = call_args[1]["params"]
        assert params["q"] == "test query"
        assert params["agent"] == "test-agent"
        assert params["limit"] == 10
        assert params["collection"] == "workspace"


class TestCalculateAgentBudget:
    """Tests for calculate_agent_budget function."""

    def test_no_model_returns_default(self):
        """Test that missing model returns default budget."""
        budget = calculate_agent_budget({}, None)
        assert budget == 4000  # DEFAULT_HISTORY_MAX_TOKENS

    def test_empty_model_returns_default(self):
        """Test that empty model returns default budget."""
        budget = calculate_agent_budget({"model": ""}, None)
        assert budget == 4000

    @patch("tinyagentos.cluster.model_resolver._find_model_manifest")
    def test_model_with_manifest_uses_context_window(self, mock_find_manifest):
        """Test that model with manifest uses context window for budget."""
        mock_manifest = MagicMock()
        mock_manifest.context_window = 8192
        mock_find_manifest.return_value = mock_manifest

        budget = calculate_agent_budget({"model": "test-model"}, MagicMock())
        # history_token_budget(8192) = max(512, 8192 - 10000 - 1024) = max(512, -2832) = 512
        assert budget == 512

    @patch("tinyagentos.cluster.model_resolver._find_model_manifest")
    def test_large_context_window(self, mock_find_manifest):
        """Test budget calculation for large context window."""
        mock_manifest = MagicMock()
        mock_manifest.context_window = 128000
        mock_find_manifest.return_value = mock_manifest

        budget = calculate_agent_budget({"model": "large-model"}, MagicMock())
        # 128000 - 10000 - 1024 = 116976
        assert budget == 116976

    @patch("tinyagentos.cluster.model_resolver._find_model_manifest")
    def test_unknown_model_returns_default(self, mock_find_manifest):
        """Test that unknown model (no manifest) returns default."""
        mock_find_manifest.return_value = None

        budget = calculate_agent_budget({"model": "unknown-model"}, MagicMock())
        assert budget == 4000


class TestGetTaOSmdClient:
    """Tests for get_taosmd_client function."""

    @patch("tinyagentos.routes.user_memory._taosmd_base")
    def test_returns_client_with_request_config(self, mock_taosmd_base):
        """Test that get_taosmd_client returns configured client."""
        mock_taosmd_base.return_value = "http://custom:7900"
        mock_request = MagicMock()
        mock_http_client = AsyncMock()
        mock_request.app.state.http_client = mock_http_client
        mock_request.app.state.config = MagicMock()

        client = get_taosmd_client(mock_request)

        assert isinstance(client, TaOSmdClient)
        assert client._base_url == "http://custom:7900"
        assert client._http == mock_http_client


class TestAnswerContextResult:
    """Tests for AnswerContextResult dataclass."""

    def test_dataclass_fields(self):
        """Test AnswerContextResult has all required fields."""
        result = AnswerContextResult(
            context="test context",
            dropped=5,
            layers={"l0": 10, "l1": 20},
            total_tokens=30,
            intent="question",
            latency_ms=15.5,
        )

        assert result.context == "test context"
        assert result.dropped == 5
        assert result.layers == {"l0": 10, "l1": 20}
        assert result.total_tokens == 30
        assert result.intent == "question"
        assert result.latency_ms == 15.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])