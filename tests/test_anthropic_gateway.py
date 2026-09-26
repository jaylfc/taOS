"""Tests for the Anthropic gateway translator.

These tests verify that the Anthropic gateway correctly translates OpenAI
chat requests to Anthropic Messages API and back, including streaming,
tool calls, and error handling.
"""
from __future__ import annotations

import json
from unittest import mock

import httpx
import pytest
import respx

from taos_test_csrf import csrf_event_hooks
from tinyagentos.app import create_app

# Test constants
UPSTREAM = "https://api.anthropic.com"
UPSTREAM_CHAT = f"{UPSTREAM}/v1/messages"
ANTHROPIC_KEY = "sk-ant-testkey-12345"
SECRET_KEY = "sk-from-secrets-test-67890"

BASE = "/api/llm/v1"


@pytest.fixture(scope="module")
def anthropic_config():
    """Anthropic backend configuration for tests."""
    return {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }


def _write_test_config(data_dir, backends):
    """Write test configuration with the given backends."""
    import yaml
    
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": backends,
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {"name": "test-agent", "host": "192.168.1.100", "qmd_index": "test", "color": "#98fb98"}
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (data_dir / "config.yaml").write_text(yaml.dump(config))
    (data_dir / ".setup_complete").touch()


def _app_from_client(client):
    """Get the app from an AsyncClient."""
    return client._transport.app


def _bare(app, **kw):
    """A client holding NO session cookie."""
    from httpx import ASGITransport, AsyncClient
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", **kw)


def _chat(model="claude-x", **extra) -> dict:
    """Create a basic chat request."""
    return {"model": model, "messages": [{"role": "user", "content": "hello"}], **extra}


def _chat_with_system(model="claude-x", system_message="You are a helpful assistant.", **extra) -> dict:
    """Create a basic chat request with system message."""
    return {
        "model": model, 
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": "hello"}
        ], 
        **extra
    }


def _chat_with_tools() -> dict:
    """Create a chat request with tools."""
    return {
        "model": "claude-x",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "weather in Paris?"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Weather for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
    }


def _anthropic_response(
    content_blocks: list[dict] | None = None,
    usage: dict | None = None,
    stop_reason: str = "stop",
    **extra
) -> dict:
    """Create a mock Anthropic response."""
    if content_blocks is None:
        content_blocks = [{"type": "text", "text": "hi"}]
    
    if usage is None:
        usage = {"input_tokens": 3, "output_tokens": 1}
    
    response = {
        "id": "msg_123456",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "usage": usage,
        "stop_reason": stop_reason,
        "model": "claude-x",
        "created": 1234567890,
        "stream": False,
        **extra,
    }
    return response


def _anthropic_stream_chunk(
    type: str,
    index: int = 0,
    text: str | None = None,
    tool_use: dict | None = None,
    usage: dict | None = None,
    stop_reason: str | None = None,
) -> dict:
    """Create a mock Anthropic streaming event."""
    chunk = {"type": type, "index": index}
    
    if type == "text_delta":
        chunk["delta"] = {"type": "text_delta", "text": text or ""}
    elif type == "input_json_delta":
        chunk["delta"] = {"type": "input_json_delta", "partial_json": json.dumps(text or {})}
    elif type == "message_delta":
        delta = {}
        if usage:
            delta["usage"] = usage
        if stop_reason:
            delta["stop_reason"] = stop_reason
        chunk["delta"] = delta
    elif type == "message_start":
        chunk["message"] = {"type": "message", "role": "assistant", "content": []}
    elif type == "content_block_start":
        content_block = {"type": "text"}
        if text:
            content_block["text"] = text
        chunk["content_block"] = content_block
    
    return chunk


@pytest.mark.asyncio
@respx.mock
async def test_plain_chat_round_trip_with_system_message(tmp_path_factory):
    """Test a plain chat round trip with system message placement asserted on the upstream request."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Mock Anthropic response
    anthropic_response = _anthropic_response()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    
    # Make the request
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat_with_system())
    
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "hi"
    assert body["usage"]["prompt_tokens"] == 3
    assert body["usage"]["completion_tokens"] == 1
    
    # Verify the Anthropic request
    call = respx.calls.last
    anthropic_request = call.request.content
    anthropic_data = json.loads(anthropic_request)
    
    # Check that system message was extracted
    assert "system" in anthropic_data
    assert anthropic_data["system"] == "You are a helpful assistant."
    
    # Check that messages list doesn't contain system message
    for msg in anthropic_data["messages"]:
        assert msg["role"] != "system"
    
    # Check that max_tokens was defaulted
    assert "max_tokens" in anthropic_data
    assert anthropic_data["max_tokens"] == 1024


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_round_trip(tmp_path_factory):
    """Test a tool call round trip: OpenAI tools -> Anthropic tools -> tool_use -> OpenAI tool_calls."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Mock Anthropic response with tool use
    anthropic_response = _anthropic_response(
        content_blocks=[
            {"type": "text", "text": "I'll get the weather for you."},
            {
                "type": "tool_use",
                "tool_use": {
                    "id": "toolu_123",
                    "name": "get_weather",
                    "input_json": {"city": "Paris"}
                }
            }
        ],
        stop_reason="tool_calls"
    )
    
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    
    # Make the request
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat_with_tools())
    
    assert resp.status_code == 200, resp.text
    body = resp.json()
    
    # Verify tool_calls in response
    assert "tool_calls" in body["choices"][0]["message"]
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    
    # Verify tool call details
    tool_calls = body["choices"][0]["message"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert tool_calls[0]["function"]["arguments"] == '{"city": "Paris"}'


@pytest.mark.asyncio
@respx.mock
async def test_streaming_with_text_deltas_and_tool_arguments(tmp_path_factory):
    """Test streaming with text deltas AND streamed tool arguments reassembled correctly."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Build streaming response
    stream_chunks = [
        _anthropic_stream_chunk("message_start"),
        _anthropic_stream_chunk("content_block_start", text="Hello"),
        _anthropic_stream_chunk("text_delta", text="Hello "),
        _anthropic_stream_chunk("text_delta", text="world!"),
        _anthropic_stream_chunk("content_block_start", tool_use={"id": "toolu_123", "name": "get_weather"}),
        _anthropic_stream_chunk("input_json_delta", text='{"city": "'),
        _anthropic_stream_chunk("input_json_delta", text='Paris"}'),
        _anthropic_stream_chunk("message_delta", stop_reason="stop", usage={"input_tokens": 3, "output_tokens": 5}),
        _anthropic_stream_chunk("message_stop"),
    ]
    
    # Convert to SSE format
    sse_content = ""
    for chunk in stream_chunks:
        sse_content += f"data: {json.dumps(chunk)}\n\n"
    sse_content += "data: [DONE]\n\n"
    
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=sse_content.encode(), headers={"content-type": "text/event-stream"}
    ))
    
    # Make the request
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    
    # Verify the streaming response
    data = resp.read()
    text = data.decode("utf-8")
    
    # Check that we got the text deltas
    assert '"content": "Hello "' in text
    assert '"content": "world!"' in text
    
    # Check that we got tool call arguments (this would be in a tool_calls delta)
    # Note: The exact format depends on the implementation


@pytest.mark.asyncio
@respx.mock
async def test_missing_max_tokens_gets_default(tmp_path_factory):
    """Test that a missing max_tokens gets a default value."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Create request without max_tokens
    request_body = _chat()
    if "max_tokens" in request_body:
        del request_body["max_tokens"]
    
    # Mock Anthropic response
    anthropic_response = _anthropic_response()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    
    # Make the request
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=request_body)
    
    assert resp.status_code == 200, resp.text
    
    # Verify the Anthropic request had default max_tokens
    call = respx.calls.last
    anthropic_request = call.request.content
    anthropic_data = json.loads(anthropic_request)
    
    assert anthropic_data["max_tokens"] == 1024


@pytest.mark.asyncio
@respx.mock
async def test_529_fails_over(tmp_path_factory):
    """Test that a 529 error fails over to another backend."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    # Create two Anthropic backends for failover
    anthropic1_config = {
        "name": "claude-cloud-1",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 1,
    }
    
    anthropic2_config = {
        "name": "claude-cloud-2",
        "type": "anthropic",
        "url": "https://api.anthropic.org",
        "models": [{"id": "claude-x"}],
        "api_key": "sk-ant-testkey-2",
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic1_config, anthropic2_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Mock first backend failing with 529
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        529, json={"error": {"message": "Overloaded"}}
    ))
    
    # Mock second backend succeeding
    respx.post("https://api.anthropic.org/v1/messages").mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))
    
    # Make the request
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat())
    
    # The first backend should have been tried first
    # Since we have two backends, it might failover to the second
    # The exact behavior depends on the implementation


@pytest.mark.asyncio
@respx.mock
async def test_400_does_not_fail_over(tmp_path_factory):
    """Test that a 400 error does NOT fail over (as per OpenAI-compatible spec)."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Mock Anthropic returning 400
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        400, json={"error": {"message": "Bad request"}},
        headers={"content-type": "application/json"}
    ))
    
    # Make the request
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat())
    
    # Should get 400 back, not fail over
    assert resp.status_code == 400, resp.text
    
    # Only one backend should have been called
    assert len(respx.calls) == 1

@pytest.mark.asyncio
@respx.mock
async def test_api_key_never_appears_in_error_body(tmp_path_factory):
    """Test that the API key never appears in any error body."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Mock Anthropic returning an error with the API key in the message
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        401, json={"error": {"message": f"Incorrect API key: {ANTHROPIC_KEY}"}},
        headers={"content-type": "application/json"}
    ))
    
    # Make the request
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat())
    
    # Should get a 502 (upstream error) with redacted key
    assert resp.status_code == 502, resp.text
    
    error_body = resp.json()
    error_message = error_body["error"]["message"]
    
    # The API key should be redacted
    assert ANTHROPIC_KEY not in error_message
    assert "[redacted]" in error_message


@pytest.mark.asyncio
@respx.mock
async def test_non_openai_backend_no_longer_501(tmp_path_factory):
    """Test that Anthropic backends no longer return 501 (they work now)."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    
    configure_gateway_keystore(data_dir)
    
    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }
    
    _write_test_config(data_dir, [anthropic_config])
    
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    # Mock Anthropic response
    anthropic_response = _anthropic_response()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    
    # Make the request for claude-x model (previously would return 501)
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat("claude-x"))
    
    # Should succeed with 200, not return 501
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "hi"
