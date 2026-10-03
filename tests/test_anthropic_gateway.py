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


# ---------------------------------------------------------------------------
# Upstream fixtures. Every shape below is copied from Anthropic's published
# Messages API docs, NOT from the translator under test:
#   https://platform.claude.com/docs/en/build-with-claude/streaming
#     ("Full HTTP stream response" / "Streaming request with tool use")
#   https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
#     ("Model responses with tools": a FLAT tool_use block, input an object)
#   https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
#     (stop_reason is end_turn | max_tokens | stop_sequence | tool_use | ...)
# ---------------------------------------------------------------------------


def _anthropic_response(
    content_blocks: list[dict] | None = None,
    usage: dict | None = None,
    stop_reason: str = "end_turn",
) -> dict:
    """A Messages API response body as the docs show it."""
    if content_blocks is None:
        content_blocks = [{"type": "text", "text": "hi"}]
    if usage is None:
        usage = {"input_tokens": 3, "output_tokens": 1}
    return {
        "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
        "type": "message",
        "role": "assistant",
        "model": "claude-x",
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


# The tool_use block from "Model responses with tools" (define-tools doc).
DOC_TOOL_USE_CONTENT = [
    {
        "type": "text",
        "text": "I'll help you check the current weather and time in San Francisco.",
    },
    {
        "type": "tool_use",
        "id": "toolu_01A09q90qw90lq917835lq9",
        "name": "get_weather",
        "input": {"location": "San Francisco, CA"},
    },
]


def _sse(event: dict) -> str:
    """One SSE frame as Anthropic sends it: a named ``event:`` line, then ``data:``."""
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


# "Streaming request with tool use" from the streaming doc, event for event
# (text deltas shortened; the input_json_delta pieces are verbatim, including
# the empty first one). No trailing [DONE]: Anthropic never sends one.
DOC_TOOL_STREAM = [
    {"type": "message_start", "message": {"id": "msg_014p7gG3wDgGV9EUtLvnow3U", "type": "message", "role": "assistant", "model": "claude-x", "stop_sequence": None, "usage": {"input_tokens": 472, "output_tokens": 2}, "content": [], "stop_reason": None}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "ping"},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Okay"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ", let's check"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_01T1x1fJ34qAmk2tNTrN7Up6", "name": "get_weather", "input": {}}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"location\":"}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": " \"San"}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": " Francisc"}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "o,"}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": " CA\"}"}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 89}},
    {"type": "message_stop"},
]

# "Basic streaming request" from the streaming doc.
DOC_TEXT_STREAM = [
    {"type": "message_start", "message": {"id": "msg_1nZdL29xx5MUA1yADyHTEsnR8uuvGzszyY", "type": "message", "role": "assistant", "content": [], "model": "claude-x", "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 25, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "ping"},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "!"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 15}},
    {"type": "message_stop"},
]


def _stream_body(events: list[dict]) -> bytes:
    return "".join(_sse(e) for e in events).encode("utf-8")


def _openai_frames(text: str) -> tuple[list[dict], bool]:
    """Parse the gateway's SSE output: (JSON chunks, saw a final [DONE])."""
    chunks: list[dict] = []
    done = False
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: "), f"not an OpenAI SSE frame: {frame!r}"
        payload = frame[len("data: "):]
        assert not done, "a frame arrived after data: [DONE]"
        if payload == "[DONE]":
            done = True
            continue
        chunks.append(json.loads(payload))
    return chunks, done


async def _gateway_app(tmp_path_factory, backends: list[dict] | None = None):
    """The real app with the gateway on, a logged-in session, and the given backends."""
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore

    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    configure_gateway_keystore(data_dir)
    _write_test_config(data_dir, backends or [{
        "name": "claude-cloud",
        "type": "anthropic",
        "url": UPSTREAM,
        "models": [{"id": "claude-x"}],
        "api_key": ANTHROPIC_KEY,
        "priority": 2,
    }])
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    state = app.state
    for store in (state.desktop_settings, state.secrets, state.agent_model_keys):
        if store._db is not None:
            await store.close()
        await store.init()
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    return app, session


async def _post(app, session, body: dict) -> httpx.Response:
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        return await client.post(BASE + "/chat/completions", json=body)


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
    
    # The doc's tool_use response: a text block, then a FLAT tool_use block.
    anthropic_response = _anthropic_response(
        content_blocks=DOC_TOOL_USE_CONTENT, stop_reason="tool_use",
    )
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat_with_tools())

    assert resp.status_code == 200, resp.text
    choice = resp.json()["choices"][0]
    message = choice["message"]
    tool_calls = message["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert tool_calls[0]["id"] == "toolu_01A09q90qw90lq917835lq9"
    assert tool_calls[0]["type"] == "function"
    # OpenAI carries arguments as a JSON STRING; Anthropic's input is an object.
    arguments = tool_calls[0]["function"]["arguments"]
    assert isinstance(arguments, str)
    assert json.loads(arguments) == {"location": "San Francisco, CA"}
    # The text Claude wrote before the call is kept alongside it.
    assert message["content"] == DOC_TOOL_USE_CONTENT[0]["text"]
    assert choice["finish_reason"] == "tool_calls"


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
    
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=_stream_body(DOC_TOOL_STREAM), headers={"content-type": "text/event-stream"}
    ))

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
    chunks, done = _openai_frames(resp.read().decode("utf-8"))
    assert done, "the stream must end with data: [DONE]"
    assert chunks, "no OpenAI chunks were emitted"
    for chunk in chunks:
        assert chunk["object"] == "chat.completion.chunk", chunk

    deltas = [c["choices"][0]["delta"] for c in chunks if c.get("choices")]
    text = "".join(d.get("content") or "" for d in deltas)
    assert text == "Okay, let's check"

    tool_deltas = [tc for d in deltas for tc in (d.get("tool_calls") or [])]
    assert tool_deltas, "no tool_calls deltas were emitted"
    first = tool_deltas[0]
    assert first["index"] == 0
    assert first["id"] == "toolu_01T1x1fJ34qAmk2tNTrN7Up6"
    assert first["type"] == "function"
    assert first["function"]["name"] == "get_weather"
    for tc in tool_deltas:
        assert tc["index"] == 0
        assert isinstance(tc["function"]["arguments"], str), tc
    arguments = "".join(tc["function"]["arguments"] for tc in tool_deltas)
    assert json.loads(arguments) == {"location": "San Francisco, CA"}

    finishes = [c["choices"][0]["finish_reason"] for c in chunks
                if c.get("choices") and c["choices"][0].get("finish_reason")]
    assert finishes == ["tool_calls"]


@pytest.mark.asyncio
@respx.mock
async def test_streaming_plain_text_maps_end_turn_and_usage(tmp_path_factory):
    """The doc's basic text stream: named event frames, a ping, end_turn, and
    usage split across message_start (input) and message_delta (output)."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=_stream_body(DOC_TEXT_STREAM), headers={"content-type": "text/event-stream"}
    ))
    resp = await _post(app, session, _chat(stream=True))
    assert resp.status_code == 200, resp.text
    chunks, done = _openai_frames(resp.read().decode("utf-8"))
    assert done
    for chunk in chunks:
        assert chunk["object"] == "chat.completion.chunk", chunk
    deltas = [c["choices"][0]["delta"] for c in chunks if c.get("choices")]
    assert "".join(d.get("content") or "" for d in deltas) == "Hello!"
    assert not any(d.get("tool_calls") for d in deltas)
    finishes = [c["choices"][0]["finish_reason"] for c in chunks
                if c.get("choices") and c["choices"][0].get("finish_reason")]
    assert finishes == ["stop"]
    usages = [c["usage"] for c in chunks if c.get("usage")]
    assert usages, "no usage chunk was emitted"
    assert usages[-1] == {"prompt_tokens": 25, "completion_tokens": 15, "total_tokens": 40}


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(("stop_reason", "finish_reason", "warns"), [
    ("end_turn", "stop", False),
    ("stop_sequence", "stop", False),
    ("max_tokens", "length", False),
    ("tool_use", "tool_calls", False),
    ("some_future_reason", "stop", True),
])
async def test_stop_reason_maps_to_openai_finish_reason(
    tmp_path_factory, caplog, stop_reason, finish_reason, warns,
):
    """Every stop_reason the card names maps to its OpenAI finish_reason;
    anything else becomes "stop" AND is logged."""
    app, session = await _gateway_app(tmp_path_factory)
    content = DOC_TOOL_USE_CONTENT if stop_reason == "tool_use" else None
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=_anthropic_response(content_blocks=content, stop_reason=stop_reason),
    ))
    with caplog.at_level("WARNING", logger="tinyagentos.llm_gateway.anthropic"):
        resp = await _post(app, session, _chat())
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["finish_reason"] == finish_reason
    logged = [r for r in caplog.records
              if r.name == "tinyagentos.llm_gateway.anthropic" and stop_reason in r.getMessage()]
    assert bool(logged) == warns, [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_unmocked_upstream_request_is_refused_not_sent(tmp_path_factory):
    """The suite cannot reach the real api.anthropic.com: under a respx router
    with assert_all_mocked, a request no route matches is refused inside the
    test process instead of going to the network."""
    app, session = await _gateway_app(tmp_path_factory)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        with pytest.raises(respx.models.AllMockedAssertionError):
            await _post(app, session, _chat())
        assert not router.routes
    # The module-level router every @respx.mock test in the gateway suites
    # uses is strict in the same way.
    assert respx.mock._assert_all_mocked is True


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


# ---------------------------------------------------------------------------
# RED-FIRST tests for error handling, streaming errors, tool_choice, and input_schema.
# Each test asserts one missing behavior and MUST FAIL on the unpatched code.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_retry_after_header(tmp_path_factory):
    """A 429 upstream returns retry-after header and code rate_limit_exceeded."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        429,
        headers={"retry-after": "2", "content-type": "application/json"},
        json={"error": {"message": "Rate limited"}},
    ))
    resp = await _post(app, session, _chat())
    assert resp.status_code == 429, resp.text
    assert resp.headers.get("retry-after") == "2"
    body = resp.json()
    assert body["error"]["type"] == "rate_limit_error"
    assert body["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
@respx.mock
async def test_stream_429(tmp_path_factory):
    """A streaming 429 must be a real 429 response, not HTTP 200."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        429,
        headers={"retry-after": "2", "content-type": "application/json"},
        json={"error": {"message": "Rate limited"}},
    ))
    resp = await _post(app, session, _chat(stream=True))
    assert resp.status_code == 429, resp.text
    assert resp.headers.get("retry-after") == "2"


@pytest.mark.asyncio
@respx.mock
async def test_stream_non_json(tmp_path_factory):
    """A streaming non-JSON error body must not crash; return the real status."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        502,
        content=b"<html>Bad Gateway</html>",
        headers={"content-type": "text/html"},
    ))
    resp = await _post(app, session, _chat(stream=True))
    assert resp.status_code == 502, resp.text


@pytest.mark.asyncio
@respx.mock
async def test_tool_choice_none(tmp_path_factory):
    """tool_choice 'none' maps to {'type': 'none'} and tools are still sent."""
    app, session = await _gateway_app(tmp_path_factory)
    anthropic_response = _anthropic_response()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    body = _chat_with_tools()
    body["tool_choice"] = "none"
    await _post(app, session, body)
    data = _upstream_json(respx.calls.last)
    assert data["tools"]
    assert data["tool_choice"] == {"type": "none"}


@pytest.mark.asyncio
@respx.mock
async def test_no_parameters(tmp_path_factory):
    """A tool with no parameters gets input_schema {'type': 'object', 'properties': {}}."""
    app, session = await _gateway_app(tmp_path_factory)
    anthropic_response = _anthropic_response()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    body = {
        "model": "claude-x",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Weather for a city",
            },
        }],
    }
    await _post(app, session, body)
    data = _upstream_json(respx.calls.last)
    tool = data["tools"][0]
    assert tool["input_schema"] == {"type": "object", "properties": {}}


def _upstream_json(call):
    return json.loads(call.request.content)


def _assert_tool_result_block(content: list | str, tool_use_id: str):
    """A user message with a single tool_result content block."""
    if isinstance(content, str):
        parsed = json.loads(content)
    else:
        parsed = content
    assert isinstance(parsed, list), "tool message should be a list of content blocks"
    assert len(parsed) == 1, parsed
    assert parsed[0] == {"type": "tool_result", "tool_use_id": tool_use_id, "content": "42"}


@pytest.mark.asyncio
@respx.mock
async def test_upstream_request_includes_model(tmp_path_factory):
    """The upstream Messages API request must carry the route's upstream model id."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))
    await _post(app, session, _chat())
    assert _upstream_json(respx.calls.last)["model"] == "claude-x"


@pytest.mark.asyncio
@respx.mock
async def test_stream_path_sets_stream_true_only(tmp_path_factory):
    """stream=true only on the streaming path; absent on non-streaming."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))
    await _post(app, session, _chat(stream=True))
    assert _upstream_json(respx.calls.last).get("stream") is True

    respx.reset()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))
    await _post(app, session, _chat())
    assert "stream" not in _upstream_json(respx.calls.last)


@pytest.mark.asyncio
@respx.mock
async def test_tools_and_tool_choice_converted_to_anthropic_shape(tmp_path_factory):
    """OpenAI tools and tool_choice are converted to Anthropic format."""
    app, session = await _gateway_app(tmp_path_factory)
    anthropic_response = _anthropic_response(
        content_blocks=DOC_TOOL_USE_CONTENT, stop_reason="tool_use",
    )
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    await _post(app, session, _chat_with_tools())
    data = _upstream_json(respx.calls.last)
    assert data["tools"] == [{
        "name": "get_weather",
        "description": "Weather for a city",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
    }]
    assert data["tool_choice"] == {"type": "tool", "name": "get_weather"}


@pytest.mark.asyncio
@respx.mock
async def test_tool_result_round_trip_in_anthropic_shape(tmp_path_factory):
    """A tool-result message becomes a user message with a tool_result block,
    and an assistant's tool_calls become tool_use blocks with parsed input."""
    app, session = await _gateway_app(tmp_path_factory)
    anthropic_response = _anthropic_response(
        content_blocks=[{
            "type": "tool_use",
            "id": "toolu_abc",
            "name": "get_weather",
            "input": {"city": "Paris"},
        }],
        stop_reason="tool_use",
    )
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json=anthropic_response, headers={"content-type": "application/json"}
    ))
    body = {
        "model": "claude-x",
        "messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "42"},
        ],
    }
    resp = await _post(app, session, body)
    assert resp.status_code == 200, resp.text
    data = _upstream_json(respx.calls.last)
    msgs = data["messages"]
    assert msgs[-2]["role"] == "assistant"
    assert msgs[-2]["content"] == [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Paris"}}]
    assert msgs[-1]["role"] == "user"
    _assert_tool_result_block(msgs[-1]["content"], "call_1")


@pytest.mark.asyncio
@respx.mock
async def test_429_becomes_openai_error_with_retry_after(tmp_path_factory):
    """A 429 upstream returns a 429 OpenAI error body with Retry-After."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        429,
        headers={"retry-after": "2", "content-type": "application/json"},
        json={"error": {"message": "Rate limited"}},
    ))
    resp = await _post(app, session, _chat())
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["error"]["type"] == "rate_limit_error"
    assert body["error"]["message"] == "the Anthropic API failed (HTTP 429): Rate limited"


@pytest.mark.asyncio
@respx.mock
async def test_streaming_500_becomes_error_not_empty_stream(tmp_path_factory):
    """A streaming 500 must be a real 502 error, not an empty stream."""
    app, session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        500, json={"error": {"message": "Server error"}},
        headers={"content-type": "application/json"},
    ))
    resp = await _post(app, session, _chat(stream=True))
    assert resp.status_code == 502, resp.text
    body = resp.json()
    assert body["error"]["type"] == "api_error"

@pytest.mark.asyncio
@respx.mock
async def test_anthropic_route_url_is_used(tmp_path_factory):
    """Test that the route's configured URL is used for the Anthropic API call."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    configure_gateway_keystore(data_dir)
    _clear_cooldowns()
    
    # Create an Anthropic backend with a custom URL
    anthropic_config = {
        "name": "claude-cloud-custom",
        "type": "anthropic",
        "url": "https://api.anthropic.org",
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
    
    # Mock the custom URL, NOT the default Anthropic URL
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
    
    # Should get 200 from the custom URL
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_usage_records_unknown_not_zero(tmp_path_factory):
    """When Anthropic returns no usage, the response must omit the usage block
    and the trace must be marked usage_estimated."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    configure_gateway_keystore(data_dir)
    _clear_cooldowns()

    anthropic_config = {
        "name": "claude-cloud-no-usage",
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

    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, json={"id": "msg_01", "type": "message", "role": "assistant", "model": "claude-x",
                    "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"},
        headers={"content-type": "application/json"}
    ))

    from httpx import ASGITransport, AsyncClient
    from tinyagentos.llm_gateway import forward as _fw
    trace_calls = []
    async def fake_record_trace(*args, **kwargs):
        trace_calls.append({"args": args, "kwargs": kwargs})

    with mock.patch.object(_fw, "_record_trace", side_effect=fake_record_trace):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": session},
            event_hooks=csrf_event_hooks(),
        ) as client:
            resp = await client.post(BASE + "/chat/completions", json=_chat())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "usage" not in body, f"usage block should be omitted when unknown, got {body.get('usage')}"

    assert trace_calls, "no trace was recorded"
    last = trace_calls[-1]
    estimated = last.get("kwargs", {}).get("estimated") or (last.get("args") or [None])[10]
    assert estimated is True, \
        f"trace should be marked usage_estimated, got {estimated}"


# ---------------------------------------------------------------------------
# RED-FIRST tests for items 1-5. Each MUST FAIL on head and PASS after fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_default_anthropic_url_does_not_double_v1(tmp_path_factory):
    """The default Anthropic URL https://api.anthropic.com/v1 must not become /v1/v1/messages."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    configure_gateway_keystore(data_dir)
    _clear_cooldowns()

    anthropic_config = {
        "name": "claude-cloud",
        "type": "anthropic",
        "url": "https://api.anthropic.com/v1",
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

    respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat())

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@respx.mock
async def test_timeout_fails_over_to_next_backend(tmp_path_factory):
    """A timeout on the first Anthropic backend must fail over to the second."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    configure_gateway_keystore(data_dir)
    _clear_cooldowns()

    anthropic1_config = {
        "name": "claude-cloud-1",
        "type": "anthropic",
        "url": "https://api.anthropic.com/v1",
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

    respx.post("https://api.anthropic.com/v1/messages").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    respx.post("https://api.anthropic.org/v1/messages").mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_streaming_fails_over_and_records_usage(tmp_path_factory):
    """A streaming 529 must fail over to the next backend, use its key,
    and record usage from the message_delta event."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore

    configure_gateway_keystore(data_dir)

    from tinyagentos.llm_gateway.forward import _clear_cooldowns
    _clear_cooldowns()

    anthropic1_config = {
        "name": "claude-cloud-1",
        "type": "anthropic",
        "url": "https://api.anthropic.com/v1",
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

    respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(
        529, json={"error": {"message": "Overloaded"}},
        headers={"content-type": "application/json"}
    ))

    stream_events = [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-x", "stop_sequence": None, "usage": {"input_tokens": 10}, "content": [], "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    respx.post("https://api.anthropic.org/v1/messages").mock(return_value=httpx.Response(
        200, content=_stream_body(stream_events), headers={"content-type": "text/event-stream"}
    ))

    from httpx import ASGITransport, AsyncClient
    from tinyagentos.llm_gateway import forward as _fw
    from tinyagentos.llm_usage.pricing import Cost
    trace_calls = []
    spend_calls = []
    async def fake_record_trace(*args, **kwargs):
        trace_calls.append({"args": args, "kwargs": kwargs})

    def fake_record_spend(state, principal, cost_usd):
        spend_calls.append({"state": state, "principal": principal, "cost_usd": cost_usd})

    with mock.patch.object(_fw, "_record_trace", side_effect=fake_record_trace), \
         mock.patch.object(_fw, "_record_spend", side_effect=fake_record_spend), \
         mock.patch("tinyagentos.llm_usage.pricing.cost_of", return_value=Cost(usd=0.001, priced=True, reason="test")):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": session},
            event_hooks=csrf_event_hooks(),
        ) as client:
            resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    chunks, done = _openai_frames(resp.read().decode("utf-8"))
    assert done
    assert any(c.get("usage", {}).get("completion_tokens") == 5 for c in chunks), \
        "usage from message_delta should be emitted"

    second_call = [c for c in respx.calls if "api.anthropic.org" in str(c.request.url)][0]
    assert second_call.request.headers["x-api-key"] == "sk-ant-testkey-2"
    assert trace_calls, "no trace was recorded for stream"
    assert any(
        (c.get("args") or [None])[5] == "claude-cloud-2"
        for c in trace_calls
    ), "trace should credit the successful backend"
    assert any(c["cost_usd"] > 0 for c in spend_calls), \
        "spend should be recorded for the successful streaming backend"


@pytest.mark.asyncio
@respx.mock
async def test_spend_recorded_on_successful_backend_after_failover(tmp_path_factory):
    """After failover, spend must be recorded on the backend that succeeded."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    configure_gateway_keystore(data_dir)
    _clear_cooldowns()

    anthropic1_config = {
        "name": "claude-cloud-1",
        "type": "anthropic",
        "url": "https://api.anthropic.com/v1",
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

    respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(
        529, json={"error": {"message": "Overloaded"}},
        headers={"content-type": "application/json"}
    ))

    respx.post("https://api.anthropic.org/v1/messages").mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))

    from httpx import ASGITransport, AsyncClient
    from tinyagentos.llm_gateway import forward as _fw
    from tinyagentos.llm_usage.pricing import Cost
    trace_calls = []
    async def fake_record_trace(*args, **kwargs):
        trace_calls.append({"args": args, "kwargs": kwargs})

    with mock.patch.object(_fw, "_record_trace", side_effect=fake_record_trace), \
         mock.patch("tinyagentos.llm_usage.pricing.cost_of", return_value=Cost(usd=0.001, priced=True, reason="test")):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": session},
            event_hooks=csrf_event_hooks(),
        ) as client:
            resp = await client.post(BASE + "/chat/completions", json=_chat())

    assert resp.status_code == 200, resp.text
    assert trace_calls, "no trace was recorded"
    last = trace_calls[-1]
    backend_name = last.get("kwargs", {}).get("backend_name") or (last.get("args") or [None])[5]
    assert backend_name == "claude-cloud-2", \
        f"trace should credit the successful backend, got {backend_name}"

    spend_calls = []
    def fake_record_spend(state, principal, cost_usd):
        spend_calls.append({"state": state, "principal": principal, "cost_usd": cost_usd})

    with mock.patch.object(_fw, "_record_spend", side_effect=fake_record_spend), \
         mock.patch("tinyagentos.llm_usage.pricing.cost_of", return_value=Cost(usd=0.001, priced=True, reason="test")):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": session},
            event_hooks=csrf_event_hooks(),
        ) as client:
            resp = await client.post(BASE + "/chat/completions", json=_chat())

    assert resp.status_code == 200, resp.text
    assert spend_calls, "no spend was recorded"
    assert any(c["cost_usd"] > 0 for c in spend_calls), \
        "spend should be recorded for the successful backend"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_failover_skips_non_anthropic_routes(tmp_path_factory):
    """Anthropic failover must skip non-Anthropic routes."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore

    configure_gateway_keystore(data_dir)

    from tinyagentos.llm_gateway.forward import _clear_cooldowns
    _clear_cooldowns()

    anthropic1_config = {
        "name": "claude-cloud-1",
        "type": "anthropic",
        "url": "https://api.anthropic.com/v1",
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

    openai_config = {
        "name": "openai-fallback",
        "type": "openai",
        "url": "https://api.openai.com/v1",
        "models": [{"id": "claude-x"}],
        "api_key": "sk-openai-test",
        "priority": 3,
    }

    _write_test_config(data_dir, [anthropic1_config, anthropic2_config, openai_config])

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

    respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(
        529, json={"error": {"message": "Overloaded"}},
        headers={"content-type": "application/json"}
    ))

    respx.post("https://api.anthropic.org/v1/messages").mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "hi"

    openai_calls = [c for c in respx.calls if "api.openai.com" in str(c.request.url)]
    assert not openai_calls, "OpenAI backend should not have been called during Anthropic failover"


@pytest.mark.asyncio
@respx.mock
async def test_529_fails_over(tmp_path_factory):
    """Test that a 529 error fails over to another backend, using route 2's key."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore

    configure_gateway_keystore(data_dir)

    from tinyagentos.llm_gateway.forward import _clear_cooldowns
    _clear_cooldowns()

    anthropic1_config = {
        "name": "claude-cloud-1",
        "type": "anthropic",
        "url": "https://api.anthropic.com/v1",
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

    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        529, json={"error": {"message": "Overloaded"}}
    ))

    respx.post("https://api.anthropic.org/v1/messages").mock(return_value=httpx.Response(
        200, json=_anthropic_response(), headers={"content-type": "application/json"}
    ))

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        resp = await client.post(BASE + "/chat/completions", json=_chat())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "hi"
    assert body["usage"]["prompt_tokens"] == 3
    assert body["usage"]["completion_tokens"] == 1

    assert len(respx.calls) >= 2, "Expected at least two calls (route 1 then route 2)"
    first_calls = [c for c in respx.calls if "api.anthropic.com" in str(c.request.url)]
    assert first_calls, "route 1 (api.anthropic.com) should have been tried first"
    second_call = [c for c in respx.calls if "api.anthropic.org" in str(c.request.url)][0]
    assert second_call.request.headers["x-api-key"] == "sk-ant-testkey-2"


@pytest.mark.asyncio
@respx.mock
async def test_streaming_unknown_usage_is_recorded_as_estimated(tmp_path_factory):
    """A stream with no message_delta usage must record usage_estimated=True
    and a conservative budget estimate."""
    data_dir = tmp_path_factory.mktemp("anthropic_gateway")
    from tinyagentos.llm_gateway.auth import configure_gateway_keystore
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    configure_gateway_keystore(data_dir)
    _clear_cooldowns()

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

    stream_events = [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-x", "stop_sequence": None, "content": [], "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}},
        {"type": "message_stop"},
    ]
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=_stream_body(stream_events), headers={"content-type": "text/event-stream"}
    ))

    from httpx import ASGITransport, AsyncClient
    from tinyagentos.llm_gateway import forward as _fw
    trace_calls = []
    spend_calls = []
    async def fake_record_trace(*args, **kwargs):
        trace_calls.append({"args": args, "kwargs": kwargs})

    def fake_record_spend(state, principal, cost_usd):
        spend_calls.append({"state": state, "principal": principal, "cost_usd": cost_usd})

    with mock.patch.object(_fw, "_record_trace", side_effect=fake_record_trace), \
         mock.patch.object(_fw, "_record_spend", side_effect=fake_record_spend):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": session},
            event_hooks=csrf_event_hooks(),
        ) as client:
            resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))

    assert resp.status_code == 200, resp.text
    chunks, done = _openai_frames(resp.read().decode("utf-8"))
    assert done
    assert not any(c.get("usage") for c in chunks), \
        "no usage chunk should be emitted when message_delta has no usage"

    assert trace_calls, "no trace was recorded for stream"
    last = trace_calls[-1]
    estimated = last.get("kwargs", {}).get("estimated") or (last.get("args") or [None])[10]
    assert estimated is True, \
        f"stream trace should be marked usage_estimated, got {estimated}"

    assert any(c["cost_usd"] > 0 for c in spend_calls), \
        "conservative budget estimate should be recorded when usage is unknown"


# ---------------------------------------------------------------------------
# tsk-7qd5nb: an aborted or errored stream is still charged, deterministically.
# ---------------------------------------------------------------------------


def _anthropic_route():
    from tinyagentos.llm_gateway.resolve import Route
    return Route(
        model_name="claude-x",
        provider="anthropic",
        upstream_model="claude-x",
        api_base=UPSTREAM,
        api_key_ref=ANTHROPIC_KEY,
        backend_name="claude-cloud",
    )


class _BytesThenReadError(httpx.AsyncByteStream):
    """Sends some SSE bytes, then the connection drops mid-stream."""

    def __init__(self, body: bytes):
        self._body = body

    async def __aiter__(self):
        yield self._body
        raise httpx.ReadError("connection reset mid-stream")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
@respx.mock
async def test_stream_client_aclose_records_spend_before_aclose_returns(tmp_path_factory):
    """Closing the OUTER stream generator after a couple of chunks must record
    the trace and spend before aclose() returns, with no gc or sleep, and
    charge an estimate since no message_delta usage arrived."""
    from tinyagentos.llm_gateway import forward as _fw
    from tinyagentos.llm_gateway.anthropic import chat_completion_stream_anthropic
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    _clear_cooldowns()
    app, _session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=_stream_body(DOC_TEXT_STREAM), headers={"content-type": "text/event-stream"}
    ))

    trace_calls: list = []
    spend_calls: list = []

    async def fake_record_trace(*args, **kwargs):
        trace_calls.append({"args": args, "kwargs": kwargs})

    def fake_record_spend(state, principal, cost_usd):
        spend_calls.append(cost_usd)

    with mock.patch.object(_fw, "_record_trace", side_effect=fake_record_trace), \
         mock.patch.object(_fw, "_record_spend", side_effect=fake_record_spend):
        agen = chat_completion_stream_anthropic(
            [_anthropic_route()], _chat(stream=True), "test-agent", app.state,
        )
        await agen.__anext__()
        await agen.__anext__()
        assert spend_calls == [] and trace_calls == []
        await agen.aclose()

        assert trace_calls, "an aborted stream must record its trace before aclose returns"
        assert spend_calls and all(c > 0 for c in spend_calls), \
            "an aborted stream must still be charged before aclose returns"
        last = trace_calls[-1]
        estimated = last["kwargs"].get("estimated", (last["args"] + (None,) * 11)[10])
        assert estimated is True, "no message_delta usage arrived, so the charge is an estimate"


@pytest.mark.asyncio
@respx.mock
async def test_stream_mid_stream_read_error_records_spend_and_re_raises(tmp_path_factory):
    """A stream that drops with httpx.ReadError after bytes were sent must
    record spend for what was streamed and still raise."""
    from tinyagentos.llm_gateway import forward as _fw
    from tinyagentos.llm_gateway.anthropic import chat_completion_stream_anthropic
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    _clear_cooldowns()
    app, _session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, stream=_BytesThenReadError(_stream_body(DOC_TEXT_STREAM[:4])),
        headers={"content-type": "text/event-stream"},
    ))

    trace_calls: list = []
    spend_calls: list = []

    async def fake_record_trace(*args, **kwargs):
        trace_calls.append({"args": args, "kwargs": kwargs})

    def fake_record_spend(state, principal, cost_usd):
        spend_calls.append(cost_usd)

    received: list[bytes] = []
    with mock.patch.object(_fw, "_record_trace", side_effect=fake_record_trace), \
         mock.patch.object(_fw, "_record_spend", side_effect=fake_record_spend):
        agen = chat_completion_stream_anthropic(
            [_anthropic_route()], _chat(stream=True), "test-agent", app.state,
        )
        with pytest.raises(httpx.ReadError):
            async for chunk in agen:
                received.append(chunk)

    assert received, "bytes must have reached the caller before the error"
    assert trace_calls, "a stream that errored mid-way must record its trace"
    assert spend_calls and all(c > 0 for c in spend_calls), \
        "a stream that errored mid-way must still be charged"


class _BytesThenHang(httpx.AsyncByteStream):
    """Sends some SSE bytes, then waits forever (until cancelled)."""

    def __init__(self, body: bytes):
        self._body = body

    async def __aiter__(self):
        import anyio
        yield self._body
        await anyio.Event().wait()

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
@respx.mock
async def test_stream_cancelled_scope_still_records_spend(tmp_path_factory):
    """A client disconnect under Starlette cancels an anyio scope, which
    re-raises at every checkpoint. The trace store awaits, so unshielded
    accounting would stop there and the abort would go uncharged."""
    import anyio
    from tinyagentos.llm_gateway import forward as _fw
    from tinyagentos.llm_gateway.anthropic import chat_completion_stream_anthropic
    from tinyagentos.llm_gateway.forward import _clear_cooldowns

    _clear_cooldowns()
    app, _session = await _gateway_app(tmp_path_factory)
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, stream=_BytesThenHang(_stream_body(DOC_TEXT_STREAM[:4])),
        headers={"content-type": "text/event-stream"},
    ))

    trace_calls: list = []
    spend_calls: list = []

    async def fake_record_trace(*args, **kwargs):
        await anyio.sleep(0)  # a checkpoint, like the real trace store's await
        trace_calls.append({"args": args, "kwargs": kwargs})

    def fake_record_spend(state, principal, cost_usd):
        spend_calls.append(cost_usd)

    received: list[bytes] = []
    with mock.patch.object(_fw, "_record_trace", side_effect=fake_record_trace), \
         mock.patch.object(_fw, "_record_spend", side_effect=fake_record_spend):
        agen = chat_completion_stream_anthropic(
            [_anthropic_route()], _chat(stream=True), "test-agent", app.state,
        )
        with anyio.CancelScope() as scope:
            async for chunk in agen:
                received.append(chunk)
                if len(received) == 2:
                    scope.cancel()

    assert len(received) == 2
    assert spend_calls and all(c > 0 for c in spend_calls), \
        "a cancelled stream must still be charged"
    assert trace_calls, "a cancelled stream must still record its trace"
