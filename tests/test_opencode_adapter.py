"""Tests for the opencode adapter.

Unit-tests cover:
  - map_opencode_event: pure codec with captured live event shapes
  - OpenCodeAdapter.prompt(): integration with monkeypatched httpx
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.adapters.opencode_adapter import (
    OpenCodeAdapter,
    OpenCodeConfig,
    map_opencode_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_state(session_id: str = "ses_abc123") -> dict:
    return {"session_id": session_id, "text": "", "done": False, "switched_model": None}


# ---------------------------------------------------------------------------
# map_opencode_event — unit tests
# ---------------------------------------------------------------------------

class TestMapOpencodeEvent:
    """Pure codec tests using event shapes captured live on the Pi."""

    # ---------------------------------------------------------------- ignored
    def test_server_connected_ignored(self):
        evt = {"type": "server.connected", "properties": {}}
        assert map_opencode_event(evt, _fresh_state()) == []

    def test_session_created_ignored(self):
        evt = {"type": "session.created", "properties": {"sessionID": "ses_abc123"}}
        assert map_opencode_event(evt, _fresh_state()) == []

    def test_message_updated_ignored(self):
        evt = {"type": "message.updated", "properties": {"sessionID": "ses_abc123", "info": {}}}
        assert map_opencode_event(evt, _fresh_state()) == []

    def test_unknown_type_returns_empty(self):
        evt = {"type": "whatever.new", "properties": {}}
        assert map_opencode_event(evt, _fresh_state()) == []

    # ---------------------------------------------------------------- text delta
    def test_text_delta_emits_delta_and_accumulates(self):
        state = _fresh_state()
        evt = {
            "type": "message.part.delta",
            "properties": {
                "sessionID": "ses_abc123",
                "messageID": "msg_001",
                "partID": "p0",
                "field": "text",
                "delta": "Hello",
            },
        }
        result = map_opencode_event(evt, state)
        assert result == [("delta", {"content": "Hello"})]
        assert state["text"] == "Hello"

    def test_text_delta_accumulates_across_calls(self):
        state = _fresh_state()
        for chunk in ["foo", " ", "bar"]:
            evt = {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": "ses_abc123",
                    "field": "text",
                    "delta": chunk,
                },
            }
            map_opencode_event(evt, state)
        assert state["text"] == "foo bar"

    def test_text_delta_ignored_for_other_session(self):
        state = _fresh_state("ses_mine")
        evt = {
            "type": "message.part.delta",
            "properties": {
                "sessionID": "ses_other",
                "field": "text",
                "delta": "noise",
            },
        }
        result = map_opencode_event(evt, state)
        assert result == []
        assert state["text"] == ""

    # ---------------------------------------------------------------- reasoning delta
    def test_reasoning_delta_emits_reasoning(self):
        state = _fresh_state()
        evt = {
            "type": "message.part.delta",
            "properties": {
                "sessionID": "ses_abc123",
                "field": "reasoning",
                "delta": "Let me think...",
            },
        }
        result = map_opencode_event(evt, state)
        assert result == [("reasoning", {"content": "Let me think..."})]
        # reasoning does NOT accumulate into text
        assert state["text"] == ""

    def test_unknown_field_in_delta_ignored(self):
        state = _fresh_state()
        evt = {
            "type": "message.part.delta",
            "properties": {
                "sessionID": "ses_abc123",
                "field": "image",
                "delta": "data:...",
            },
        }
        assert map_opencode_event(evt, state) == []

    # ---------------------------------------------------------------- tool parts
    def test_tool_part_updated_started_emits_tool_call(self):
        state = _fresh_state()
        evt = {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "tool",
                    "callID": "call_xyz",
                    "tool": "bash",
                    "input": {"cmd": "ls -la"},
                    "state": {"status": "started"},
                },
            },
        }
        result = map_opencode_event(evt, state)
        assert len(result) == 1
        kind, payload = result[0]
        assert kind == "tool_call"
        assert payload["tool"] == "bash"
        assert payload["args"] == {"cmd": "ls -la"}
        assert payload["call_id"] == "call_xyz"

    def test_tool_part_updated_success_emits_tool_result(self):
        state = _fresh_state()
        evt = {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "tool",
                    "callID": "call_xyz",
                    "tool": "bash",
                    "input": {"cmd": "ls -la"},
                    "state": {"status": "completed", "output": "file.txt\ndir/"},
                },
            },
        }
        result = map_opencode_event(evt, state)
        assert len(result) == 1
        kind, payload = result[0]
        assert kind == "tool_result"
        assert payload["success"] is True
        assert "file.txt" in payload["result"]
        assert payload["call_id"] == "call_xyz"

    def test_tool_part_updated_error_emits_tool_result_failure(self):
        state = _fresh_state()
        evt = {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "tool",
                    "callID": "call_xyz",
                    "tool": "bash",
                    "input": {},
                    "state": {"status": "error", "error": "command not found"},
                },
            },
        }
        result = map_opencode_event(evt, state)
        assert len(result) == 1
        kind, payload = result[0]
        assert kind == "tool_result"
        assert payload["success"] is False
        assert "command not found" in payload["result"]

    def test_non_tool_part_updated_ignored(self):
        state = _fresh_state()
        evt = {
            "type": "message.part.updated",
            "properties": {
                "part": {"type": "text", "content": "..."},
            },
        }
        assert map_opencode_event(evt, state) == []

    def test_tool_part_unknown_status_returns_empty(self):
        """Unknown tool part status → no emit (wait for next update)."""
        state = _fresh_state()
        evt = {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "tool",
                    "callID": "c1",
                    "tool": "search",
                    "input": {},
                    "state": {"status": "pending"},
                },
            },
        }
        assert map_opencode_event(evt, state) == []

    # ---------------------------------------------------------------- model switched
    def test_model_switched_sets_state_no_reply(self):
        state = _fresh_state()
        evt = {
            "type": "session.next.model.switched",
            "properties": {
                "sessionID": "ses_abc123",
                "model": {"providerID": "litellm", "modelID": "gpt-4o"},
            },
        }
        result = map_opencode_event(evt, state)
        assert result == []
        assert state["switched_model"] == {"providerID": "litellm", "modelID": "gpt-4o"}

    # ---------------------------------------------------------------- session.idle
    def test_session_idle_our_session_emits_final(self):
        state = _fresh_state("ses_mine")
        state["text"] = "Accumulated text"
        evt = {
            "type": "session.idle",
            "properties": {"sessionID": "ses_mine"},
        }
        result = map_opencode_event(evt, state)
        assert len(result) == 1
        kind, payload = result[0]
        assert kind == "final"
        assert payload["content"] == "Accumulated text"
        assert state["done"] is True

    def test_session_idle_other_session_ignored(self):
        state = _fresh_state("ses_mine")
        state["text"] = "Should not flush"
        evt = {
            "type": "session.idle",
            "properties": {"sessionID": "ses_other"},
        }
        result = map_opencode_event(evt, state)
        assert result == []
        assert state["done"] is False

    def test_session_idle_empty_text_emits_empty_final(self):
        state = _fresh_state("ses_1")
        evt = {
            "type": "session.idle",
            "properties": {"sessionID": "ses_1"},
        }
        result = map_opencode_event(evt, state)
        assert result == [("final", {"content": ""})]
        assert state["done"] is True

    # ---------------------------------------------------------------- defensive
    def test_missing_properties_key_is_safe(self):
        """Events without 'properties' must not raise; sessionID filter silently excludes them."""
        evt = {"type": "session.idle"}
        state = _fresh_state()
        # properties absent → no sessionID to match → excluded, no emit
        result = map_opencode_event(evt, state)
        assert isinstance(result, list)
        # state must not be corrupted
        assert state["done"] is False

    def test_none_properties_is_safe(self):
        evt = {"type": "server.connected", "properties": None}
        assert map_opencode_event(evt, _fresh_state()) == []


# ---------------------------------------------------------------------------
# OpenCodeAdapter.prompt() — integration test with monkeypatched httpx
# ---------------------------------------------------------------------------

def _build_sse_bytes(*events: dict) -> bytes:
    """Encode a sequence of dicts as SSE ``data:`` lines."""
    lines = []
    for evt in events:
        lines.append(f"data: {json.dumps(evt)}\n\n")
    return "".join(lines).encode()


class _FakeStreamResponse:
    """Minimal async context manager that yields SSE lines."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def aiter_lines(self):
        for line in self._body.decode().splitlines():
            yield line


class TestOpenCodeAdapterPrompt:
    """Integration test: monkeypatch httpx, assert the sink receives the
    reply-dict sequence ({"kind", "trace_id", ...})."""

    def _make_adapter(self, sink) -> OpenCodeAdapter:
        cfg = OpenCodeConfig(
            base_url="http://127.0.0.1:5888",
            model_provider_id="litellm",
            model_id="gpt-4o",
            agent="test-agent",
        )
        return OpenCodeAdapter(cfg, sink)

    def _sse_body(self) -> bytes:
        events = [
            {"type": "server.connected", "properties": {}},
            {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": "ses_test",
                    "field": "text",
                    "delta": "Hello",
                },
            },
            {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": "ses_test",
                    "field": "text",
                    "delta": " world",
                },
            },
            {
                "type": "session.idle",
                "properties": {"sessionID": "ses_test"},
            },
        ]
        return _build_sse_bytes(*events)

    @pytest.mark.asyncio
    async def test_prompt_calls_record_reply_in_order(self, monkeypatch):
        # Track sink reply dicts
        calls: list[dict] = []

        def sink(reply: dict):
            calls.append(reply)

        adapter = self._make_adapter(sink)
        sse_body = self._sse_body()

        # Fake POST /session → returns session id
        session_resp = MagicMock()
        session_resp.status_code = 200
        session_resp.raise_for_status = MagicMock()
        session_resp.json = MagicMock(return_value={"id": "ses_test"})

        # Fake POST /session/:id/prompt_async → returns 204
        prompt_resp = MagicMock()
        prompt_resp.status_code = 204

        # Fake GET /event stream
        stream_ctx = _FakeStreamResponse(sse_body)

        async def fake_post(url, **kwargs):
            if "/session" in url and "prompt_async" not in url:
                return session_resp
            return prompt_resp

        fake_client = MagicMock()
        fake_client.is_closed = False
        fake_client.post = AsyncMock(side_effect=fake_post)
        fake_client.stream = MagicMock(return_value=stream_ctx)
        fake_client.aclose = AsyncMock()

        async def fake_get_client():
            return fake_client

        monkeypatch.setattr(adapter, "_get_client", fake_get_client)

        await adapter.prompt("Say hello")

        # Should have: delta("Hello"), delta(" world"), final("Hello world")
        assert len(calls) == 3

        assert calls[0]["kind"] == "delta"
        assert calls[0]["content"] == "Hello"

        assert calls[1]["kind"] == "delta"
        assert calls[1]["content"] == " world"

        assert calls[2]["kind"] == "final"
        assert calls[2]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_prompt_with_trace_id_forwarded(self, monkeypatch):
        calls: list[dict] = []

        def sink(reply: dict):
            calls.append(reply)

        adapter = self._make_adapter(sink)

        sse_body = _build_sse_bytes(
            {"type": "session.idle", "properties": {"sessionID": "ses_t2"}},
        )
        stream_ctx = _FakeStreamResponse(sse_body)

        session_resp = MagicMock()
        session_resp.status_code = 200
        session_resp.raise_for_status = MagicMock()
        session_resp.json = MagicMock(return_value={"id": "ses_t2"})

        prompt_resp = MagicMock()
        prompt_resp.status_code = 204

        async def fake_post(url, **kwargs):
            if "prompt_async" not in url:
                return session_resp
            return prompt_resp

        fake_client = MagicMock()
        fake_client.is_closed = False
        fake_client.post = AsyncMock(side_effect=fake_post)
        fake_client.stream = MagicMock(return_value=stream_ctx)
        fake_client.aclose = AsyncMock()

        async def fake_get_client():
            return fake_client

        monkeypatch.setattr(adapter, "_get_client", fake_get_client)

        await adapter.prompt("hi", trace_id="trace-42")

        # Every emitted reply should carry trace_id
        assert all(c.get("trace_id") == "trace-42" for c in calls)
        # There should be a final call
        final_calls = [c for c in calls if c["kind"] == "final"]
        assert len(final_calls) == 1

    @pytest.mark.asyncio
    async def test_prompt_emits_error_on_transport_failure(self, monkeypatch):
        calls: list[dict] = []

        def sink(reply: dict):
            calls.append(reply)

        adapter = self._make_adapter(sink)

        async def fake_get_client():
            raise ConnectionError("connection refused")

        monkeypatch.setattr(adapter, "_get_client", fake_get_client)

        # Should not raise
        await adapter.prompt("hello")

        error_calls = [c for c in calls if c["kind"] == "error"]
        assert len(error_calls) == 1
        assert "connection refused" in error_calls[0].get("error", "")

    @pytest.mark.asyncio
    async def test_prompt_emits_error_when_stream_ends_early(self, monkeypatch):
        """Stream closes without session.idle → error reply."""
        calls: list[dict] = []

        def sink(reply: dict):
            calls.append(reply)

        adapter = self._make_adapter(sink)

        # SSE body ends without session.idle
        sse_body = _build_sse_bytes(
            {
                "type": "message.part.delta",
                "properties": {"sessionID": "ses_early", "field": "text", "delta": "partial"},
            },
        )
        stream_ctx = _FakeStreamResponse(sse_body)

        session_resp = MagicMock()
        session_resp.status_code = 200
        session_resp.raise_for_status = MagicMock()
        session_resp.json = MagicMock(return_value={"id": "ses_early"})

        prompt_resp = MagicMock()
        prompt_resp.status_code = 204

        async def fake_post(url, **kwargs):
            if "prompt_async" not in url:
                return session_resp
            return prompt_resp

        fake_client = MagicMock()
        fake_client.is_closed = False
        fake_client.post = AsyncMock(side_effect=fake_post)
        fake_client.stream = MagicMock(return_value=stream_ctx)
        fake_client.aclose = AsyncMock()

        async def fake_get_client():
            return fake_client

        monkeypatch.setattr(adapter, "_get_client", fake_get_client)

        await adapter.prompt("hi")

        error_calls = [c for c in calls if c["kind"] == "error"]
        assert len(error_calls) == 1
        assert "session.idle" in error_calls[0]["error"]


# ---------------------------------------------------------------------------
# OpenCodeAdapter.close() — safety checks
# ---------------------------------------------------------------------------

class TestOpenCodeAdapterClose:
    @pytest.mark.asyncio
    async def test_close_safe_when_no_client(self):
        cfg = OpenCodeConfig(base_url="http://127.0.0.1:5888")
        adapter = OpenCodeAdapter(cfg, sink=lambda r: None)
        # Should not raise
        await adapter.close()

    @pytest.mark.asyncio
    async def test_close_safe_when_already_closed(self):
        cfg = OpenCodeConfig(base_url="http://127.0.0.1:5888")
        adapter = OpenCodeAdapter(cfg, sink=lambda r: None)
        fake = MagicMock()
        fake.is_closed = True
        fake.aclose = AsyncMock()
        adapter._client = fake
        await adapter.close()
        fake.aclose.assert_not_called()
