"""Tests for the DeerFlow adapter.

The /health endpoint always returns ok. The /message endpoint surfaces a
non-empty error string when the DeerFlow backend is not running (expected in
unit tests). _extract_text pulls the assistant reply out of the serialized
LangGraph channel-values regardless of message content shape.
"""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.adapters import deer_flow_adapter
from tinyagentos.adapters.deer_flow_adapter import _extract_text


def test_extract_text_string_content():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]
    assert _extract_text(messages) == "hello there"


def test_extract_text_list_of_text_blocks():
    messages = [
        {"type": "ai", "content": [
            {"type": "text", "text": "part one "},
            {"type": "tool_use", "name": "search"},
            {"type": "text", "text": "part two"},
        ]},
    ]
    assert _extract_text(messages) == "part one part two"


def test_extract_text_empty_or_missing():
    assert _extract_text([]) == ""
    assert _extract_text(None) == ""


def test_extract_text_skips_trailing_non_assistant_message():
    """The final channel-values message may be a tool/human turn; the adapter
    must return the last ASSISTANT message, not blindly messages[-1]."""
    messages = [
        {"role": "user", "content": "search the web"},
        {"role": "assistant", "content": "here is what I found"},
        {"role": "tool", "content": "raw tool output blob"},
    ]
    assert _extract_text(messages) == "here is what I found"


def test_extract_text_fallback_when_no_assistant_role():
    """With no role/type markers, fall back to the last message's text."""
    assert _extract_text([{"content": "only message"}]) == "only message"


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=deer_flow_adapter.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["framework"] == "deer-flow"


@pytest.mark.asyncio
async def test_message_without_backend():
    """The adapter returns an error string when the DeerFlow backend is down.

    with_retry would otherwise back off for ~31 s over 7 attempts when nothing
    is listening, so patch it to a single no-delay attempt. The retry policy is
    covered by test_adapter_retry.py; this only checks the adapter never raises.
    """
    async def _single_attempt(factory, **_kwargs):
        return await factory()

    transport = ASGITransport(app=deer_flow_adapter.app)
    with patch.object(deer_flow_adapter, "with_retry", side_effect=_single_attempt):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/message", json={"text": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
    assert isinstance(data["content"], str)
    assert len(data["content"]) > 0
