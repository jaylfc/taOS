"""Tests for the Hermes API adapter (tinyagentos.adapters.hermes_api_adapter).

Covers the same contract as PicoClawCliAdapter: ensure_session, prompt, close.
Uses httpx.MockTransport for fully in-process HTTP mocking — no real network.
"""
from __future__ import annotations

import httpx
import pytest

from tinyagentos.adapters.hermes_api_adapter import HermesApiAdapter, HermesApiConfig


class TestHermesApiAdapter:
    """Unit tests for HermesApiAdapter against mocked HTTP responses."""

    def _make_adapter(self, cfg: HermesApiConfig, collected: list):
        def sink(reply: dict):
            collected.append(reply)

        return HermesApiAdapter(cfg, sink)

    def _mock_transport(self, status: int = 200, json_body: dict | None = None, raise_exc: Exception | None = None):
        """Create an httpx.MockTransport that returns a canned response or raises."""

        def handler(request: httpx.Request) -> httpx.Response:
            if raise_exc:
                raise raise_exc
            return httpx.Response(status, json=json_body or {"choices": [{"message": {"content": "ok"}}]})

        return httpx.MockTransport(handler)

    def _make_mock_client(self, transport):
        """Create a mock AsyncClient factory that returns a client with the given transport."""
        original_client = httpx.AsyncClient

        def mock_factory(*args, **kwargs):
            # Remove transport from kwargs if present, use our mock transport
            kwargs.pop("transport", None)
            return original_client(*args, transport=transport, **kwargs)

        return mock_factory

    @pytest.mark.asyncio
    async def test_prompt_success_emits_delta_and_final(self):
        """HTTP 200 with content → one delta then one final, both with the content."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="test-key", system="be nice")
        adapter = self._make_adapter(cfg, collected)

        import tinyagentos.adapters.hermes_api_adapter as adapter_module
        original_async_client = adapter_module.httpx.AsyncClient
        adapter_module.httpx.AsyncClient = self._make_mock_client(self._mock_transport(200, {"choices": [{"message": {"content": "hello world"}}]}))
        try:
            await adapter.prompt("hi", trace_id="t1")
        finally:
            adapter_module.httpx.AsyncClient = original_async_client

        assert len(collected) == 2
        assert collected[0] == {"kind": "delta", "trace_id": "t1", "content": "hello world"}
        assert collected[1] == {"kind": "final", "trace_id": "t1", "content": "hello world"}

    @pytest.mark.asyncio
    async def test_prompt_request_carries_system_message_first_and_bearer_header(self):
        """The request body must have system message first, then history, then user; header must be Bearer <key>."""
        collected = []
        cfg = HermesApiConfig(
            url="http://hermes:8642",
            key="secret-key",
            system="system prompt",
            history=[{"role": "user", "content": "old user"}, {"role": "assistant", "content": "old assistant"}],
        )
        adapter = self._make_adapter(cfg, collected)

        captured = {}

        def capture_handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["body"] = request.read().decode()
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        import tinyagentos.adapters.hermes_api_adapter as adapter_module
        original_async_client = adapter_module.httpx.AsyncClient
        adapter_module.httpx.AsyncClient = self._make_mock_client(httpx.MockTransport(capture_handler))
        try:
            await adapter.prompt("new user", trace_id="t2")
        finally:
            adapter_module.httpx.AsyncClient = original_async_client

        import json as jsonlib
        assert captured["headers"]["authorization"] == "Bearer secret-key"
        body = jsonlib.loads(captured["body"])
        assert body["model"] == "hermes"
        messages = body["messages"]
        assert messages[0] == {"role": "system", "content": "system prompt"}
        assert messages[1] == {"role": "user", "content": "old user"}
        assert messages[2] == {"role": "assistant", "content": "old assistant"}
        assert messages[3] == {"role": "user", "content": "new user"}

    @pytest.mark.asyncio
    async def test_prompt_non_200_emits_one_error_key_absent(self):
        """Non-200 status → one error reply, key NOT present in error text."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="must-not-appear")
        adapter = self._make_adapter(cfg, collected)

        import tinyagentos.adapters.hermes_api_adapter as adapter_module
        original_async_client = adapter_module.httpx.AsyncClient
        adapter_module.httpx.AsyncClient = self._make_mock_client(self._mock_transport(503, {"error": "service unavailable"}))
        try:
            await adapter.prompt("hi", trace_id="t3")
        finally:
            adapter_module.httpx.AsyncClient = original_async_client

        assert len(collected) == 1
        assert collected[0]["kind"] == "error"
        assert "503" in collected[0]["error"]
        assert "must-not-appear" not in collected[0]["error"]

    @pytest.mark.asyncio
    async def test_prompt_missing_content_emits_error(self):
        """200 but missing choices[0].message.content → one error reply."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="test-key")
        adapter = self._make_adapter(cfg, collected)

        import tinyagentos.adapters.hermes_api_adapter as adapter_module
        original_async_client = adapter_module.httpx.AsyncClient
        adapter_module.httpx.AsyncClient = self._make_mock_client(self._mock_transport(200, {"choices": [{}]}) )
        try:
            await adapter.prompt("hi", trace_id="t4")
        finally:
            adapter_module.httpx.AsyncClient = original_async_client

        assert len(collected) == 1
        assert collected[0]["kind"] == "error"
        assert "missing content" in collected[0]["error"].lower()

    @pytest.mark.asyncio
    async def test_prompt_connection_error_emits_error(self):
        """httpx.ConnectError → one error reply naming the exception type."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="test-key")
        adapter = self._make_adapter(cfg, collected)

        import tinyagentos.adapters.hermes_api_adapter as adapter_module
        original_async_client = adapter_module.httpx.AsyncClient
        adapter_module.httpx.AsyncClient = self._make_mock_client(self._mock_transport(raise_exc=httpx.ConnectError("connection refused")))
        try:
            await adapter.prompt("hi", trace_id="t5")
        finally:
            adapter_module.httpx.AsyncClient = original_async_client

        assert len(collected) == 1
        assert collected[0]["kind"] == "error"
        assert "ConnectError" in collected[0]["error"]

    @pytest.mark.asyncio
    async def test_prompt_timeout_error_emits_error(self):
        """httpx.TimeoutException → one error reply naming the exception type."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="test-key")
        adapter = self._make_adapter(cfg, collected)

        import tinyagentos.adapters.hermes_api_adapter as adapter_module
        original_async_client = adapter_module.httpx.AsyncClient
        adapter_module.httpx.AsyncClient = self._make_mock_client(self._mock_transport(raise_exc=httpx.TimeoutException("timeout")))
        try:
            await adapter.prompt("hi", trace_id="t6")
        finally:
            adapter_module.httpx.AsyncClient = original_async_client

        assert len(collected) == 1
        assert collected[0]["kind"] == "error"
        assert "TimeoutException" in collected[0]["error"]

    @pytest.mark.asyncio
    async def test_prompt_with_attachments_emits_error_no_http_call(self):
        """Non-empty attachments → one error reply, no HTTP request made."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="test-key")
        adapter = self._make_adapter(cfg, collected)

        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        import tinyagentos.adapters.hermes_api_adapter as adapter_module
        original_async_client = adapter_module.httpx.AsyncClient
        adapter_module.httpx.AsyncClient = self._make_mock_client(httpx.MockTransport(counting_handler))
        try:
            await adapter.prompt("hi", trace_id="t7", attachments=[{"mime_type": "image/png", "data_b64": "abc"}])
        finally:
            adapter_module.httpx.AsyncClient = original_async_client

        assert call_count == 0, "HTTP call must not be made when attachments are present"
        assert len(collected) == 1
        assert collected[0]["kind"] == "error"
        assert "attachments are not supported" in collected[0]["error"]

    @pytest.mark.asyncio
    async def test_ensure_session_returns_stable_id(self):
        """ensure_session returns a stable session id."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="test-key")
        adapter = HermesApiAdapter(cfg, lambda r: collected.append(r))

        id1 = await adapter.ensure_session()
        id2 = await adapter.ensure_session()
        assert id1 == id2 == "hermes-session"

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        """close() returns None and never raises."""
        collected = []
        cfg = HermesApiConfig(url="http://hermes:8642", key="test-key")
        adapter = HermesApiAdapter(cfg, lambda r: collected.append(r))

        result = await adapter.close()
        assert result is None