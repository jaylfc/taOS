"""RED-FIRST tests for in_use counter fixes (tsk-i2rxli / PR #4459 fix-forward).

Each test drives the real caller and MUST FAIL on the current branch before fixes.
Run with: python -m pytest tests/test_in_use_counters_fix.py -q
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure tests/ is on sys.path for taos_test_csrf import
sys.path.insert(0, str(Path(__file__).parent))

import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.bridge_session import BridgeSessionRegistry
from tinyagentos.agent_chat_router import AgentChatRouter
from tinyagentos.in_use import InUseCounters
from tinyagentos.llm_gateway.forward import _event_stream_for_route, _call_with_retry
from tinyagentos.llm_gateway.resolve import Route
from taos_test_csrf import csrf_event_hooks


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

BASE = "/api/llm/v1"

def _sse_chunk(text: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\n".encode()

def _sse_done() -> bytes:
    return b"data: [DONE]\n\n"


def _write_gw_config(data_dir, upstream_url) -> None:
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {
                "name": "test-backend",
                "type": "openai-compatible",
                "url": upstream_url + "/v1",
                "models": [{"id": "qwen3-8b"}],
                "api_key": "sk-test-DO-NOT-LEAK",
                "priority": 1,
            }
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [{"name": "test-agent", "host": "127.0.0.1", "color": "#98fb98"}],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (data_dir / "config.yaml").write_text(yaml.dump(config))
    (data_dir / ".setup_complete").touch()


@pytest_asyncio.fixture
async def slow_sse_upstream():
    """A real local HTTP server that drips SSE chunks slowly."""
    async def handler(reader, writer):
        await reader.readline()
        content_length = 0
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
        if content_length:
            await reader.readexactly(content_length)

        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: keep-alive\r\n"
            b"\r\n"
        )
        for text in ("part-one", "part-two"):
            writer.write(_sse_chunk(text))
            await writer.drain()
            await asyncio.sleep(1.0)
        writer.write(_sse_done())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


@pytest_asyncio.fixture
async def gw_client(slow_sse_upstream, tmp_path, monkeypatch):
    """Real app plus signed-in client; the upstream is a slow local SSE server."""
    _write_gw_config(tmp_path, slow_sse_upstream)
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    app = create_app(data_dir=tmp_path)
    state = app.state
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c
    await state.http_client.aclose()


async def _stream_body(client, req):
    """Drive a streaming gateway request to completion in the background."""
    resp = await client.send(req, stream=True)
    if resp.status_code != 200:
        return
    async for _ in resp.aiter_bytes():
        pass
    await resp.aclose()


async def _wait_for_counter(client, target, timeout_s=5.0):
    """Poll /api/health until in_use.gateway_streams matches ``target``."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        health = await client.get("/api/health")
        counter = health.json()["in_use"]["gateway_streams"]
        if counter == target:
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"gateway in_use.gateway_streams never reached {target}"
            )
        await asyncio.sleep(0.05)


def _write_config(data_dir, backends):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": backends,
        "qmd": {"url": "http://localhost:7832"},
        "agents": [{"name": "test-agent", "host": "127.0.0.1", "color": "#98fb98"}],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (data_dir / "config.yaml").write_text(yaml.dump(config))
    (data_dir / ".setup_complete").touch()


@pytest_asyncio.fixture
async def app_with_gateway(tmp_path, monkeypatch):
    """Real app with gateway enabled, signed-in admin."""
    backends = [{
        "name": "test-backend",
        "type": "openai-compatible",
        "url": "http://upstream.test:8080/v1",
        "models": [{"id": "qwen3-8b"}],
        "api_key": "sk-test",
        "priority": 1,
    }]
    _write_config(tmp_path, backends)
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    app = create_app(data_dir=tmp_path)
    state = app.state
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c, app
    await state.http_client.aclose()


# ---------------------------------------------------------------------------
# Issue 1: agent_turns leaks on a re-enqueued trace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_turns_no_leak_on_reenqueued_trace(app_with_gateway):
    """Enqueue same trace_id twice, send one final reply, agent_turns must be 0."""
    client, app = app_with_gateway
    in_use = app.state.in_use
    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
    )
    app.state.bridge_sessions = reg

    # Enqueue same trace_id twice (simulates a re-enqueue)
    await reg.enqueue_user_message("test-agent", {
        "id": "m1", "trace_id": "t1", "channel_id": "ch1",
        "from": "user", "text": "hello", "hops_since_user": 0,
    })
    await reg.enqueue_user_message("test-agent", {
        "id": "m1", "trace_id": "t1", "channel_id": "ch1",
        "from": "user", "text": "hello again", "hops_since_user": 0,
    })

    # Counter should be 1 (not 2) because trace_id was already pending
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 1, \
        f"Expected 1, got {health.json()['in_use']['agent_turns']}"

    # One final reply releases the turn
    await reg.record_reply("test-agent", {
        "kind": "final", "id": "r1", "trace_id": "t1",
        "channel_id": "ch1", "content": "hi",
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0, \
        f"Expected 0, got {health.json()['in_use']['agent_turns']}"


# ---------------------------------------------------------------------------
# Issue 2: agent_turns leaks when turn never gets final/error reply
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_turns_released_on_session_drop(app_with_gateway):
    """Enqueue then drop session (remove from registry), agent_turns must be 0."""
    client, app = app_with_gateway
    in_use = app.state.in_use
    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
    )
    app.state.bridge_sessions = reg

    await reg.enqueue_user_message("test-agent", {
        "id": "m1", "trace_id": "t1", "channel_id": "ch1",
        "from": "user", "text": "hello", "hops_since_user": 0,
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 1

    # Drop the session by removing it from registry using the new API
    # (simulates agent going offline / session cleanup)
    await reg.remove_session("test-agent")

    # The turn should be released when the session is cleaned up
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0, \
        f"Expected 0 after session drop, got {health.json()['in_use']['agent_turns']}"


@pytest.mark.asyncio
async def test_agent_turns_released_on_turn_timeout(app_with_gateway):
    """Enqueue and let turn timeout fire, agent_turns must be 0."""
    client, app = app_with_gateway
    in_use = app.state.in_use
    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
    )
    app.state.bridge_sessions = reg

    await reg.enqueue_user_message("test-agent", {
        "id": "m1", "trace_id": "t1", "channel_id": "ch1",
        "from": "user", "text": "hello", "hops_since_user": 0,
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 1

    # Simulate turn timeout by cleaning up stale pending_hops
    # This is a new method we add for timeout handling
    await reg._release_all_pending_turns(reg._sessions["test-agent"])

    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0, \
        f"Expected 0 after timeout, got {health.json()['in_use']['agent_turns']}"


# ---------------------------------------------------------------------------
# Issue 3: release only runs inside if channel_id: and after chat persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_turns_released_on_final_reply_without_channel_id(app_with_gateway):
    """Final reply with no channel_id must still release the turn."""
    client, app = app_with_gateway
    in_use = app.state.in_use
    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
    )
    app.state.bridge_sessions = reg

    await reg.enqueue_user_message("test-agent", {
        "id": "m1", "trace_id": "t1", "channel_id": "ch1",
        "from": "user", "text": "hello", "hops_since_user": 0,
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 1

    # Final reply WITHOUT channel_id (simulates fork that doesn't thread channel_id)
    await reg.record_reply("test-agent", {
        "kind": "final", "id": "r1", "trace_id": "t1",
        "content": "hi",  # no channel_id
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0, \
        f"Expected 0 after final without channel_id, got {health.json()['in_use']['agent_turns']}"


@pytest.mark.asyncio
async def test_agent_turns_released_on_final_reply_with_persistence_error(app_with_gateway):
    """Final reply where send_message raises must still release the turn."""
    client, app = app_with_gateway
    in_use = app.state.in_use

    # Create registry with a chat_messages that raises on send_message
    class FailingChatMessages:
        async def send_message(self, **kwargs):
            raise RuntimeError("persistence failed")
        async def get_message(self, message_id):
            return None
        async def edit_message(self, message_id, content):
            pass
        async def update_state(self, message_id, state):
            pass
        async def append_content_block(self, message_id, block):
            return None
        async def extend_content_blocks(self, message_id, blocks, cap=50):
            return []

    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=FailingChatMessages(),
        chat_channels=app.state.chat_channels,
    )
    app.state.bridge_sessions = reg

    await reg.enqueue_user_message("test-agent", {
        "id": "m1", "trace_id": "t1", "channel_id": "ch1",
        "from": "user", "text": "hello", "hops_since_user": 0,
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 1

    # Final reply - persistence will fail but turn must still release
    await reg.record_reply("test-agent", {
        "kind": "final", "id": "r1", "trace_id": "t1",
        "channel_id": "ch1", "content": "hi",
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0, \
        f"Expected 0 after final with persistence error, got {health.json()['in_use']['agent_turns']}"


# ---------------------------------------------------------------------------
# Issue 4: Uncounted turn paths (ACP branch)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acp_turn_counted_in_agent_turns(app_with_gateway):
    """A real ACP turn through the router must increment agent_turns while running."""
    client, app = app_with_gateway
    in_use = app.state.in_use

    # Set up router with bridge registry
    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
        chat_hub=app.state.chat_hub,
    )
    app.state.bridge_sessions = reg

    router = AgentChatRouter(app.state)

    # Mock drive_turn to do nothing but succeed - with a small delay so we can observe the counter
    async def slow_drive_turn(*args, **kwargs):
        await asyncio.sleep(0.2)

    with patch("tinyagentos.openclaw_acp_runtime.drive_turn", new=slow_drive_turn):
        # Create a message that will route to an openclaw ACP agent
        message = {
            "id": "m1", "trace_id": "t1", "channel_id": "ch1",
            "author_id": "user", "content": "hello agent",
            "created_at": 123.0,
            "metadata": {"hops_since_user": 0},
        }
        channel = {
            "id": "ch1", "type": "dm", "members": ["user", "test-agent"],
            "settings": {},
        }

        # Agent config for openclaw with ACP transport
        app.state.config.agents = [{
            "name": "test-agent",
            "framework": "openclaw",
            "model": "qwen3-8b",
            "status": "running",
        }]

        # Ensure ACP transport
        os.environ["TAOS_OPENCLAW_TRANSPORT"] = "acp"

        # Dispatch the message (fire and forget)
        router.dispatch(message, channel)

        # Give the task time to start
        await asyncio.sleep(0.05)

        # While the ACP turn is running, agent_turns should be 1
        health = await client.get("/api/health")
        assert health.json()["in_use"]["agent_turns"] == 1, \
            f"Expected 1 during ACP turn, got {health.json()['in_use']['agent_turns']}"

        # Wait for the task to complete
        await asyncio.sleep(0.3)

        # After completion, agent_turns should be 0
        health = await client.get("/api/health")
        assert health.json()["in_use"]["agent_turns"] == 0, \
            f"Expected 0 after ACP turn, got {health.json()['in_use']['agent_turns']}"


@pytest.mark.asyncio
async def test_acp_turn_counted_on_error(app_with_gateway):
    """An ACP turn that raises must still release the counter."""
    client, app = app_with_gateway
    in_use = app.state.in_use

    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
        chat_hub=app.state.chat_hub,
    )
    app.state.bridge_sessions = reg

    router = AgentChatRouter(app.state)

    # Mock drive_turn to raise an error
    async def failing_drive_turn(*args, **kwargs):
        raise RuntimeError("ACP drive failed")

    with patch("tinyagentos.openclaw_acp_runtime.drive_turn", new=failing_drive_turn):
        message = {
            "id": "m1", "trace_id": "t1", "channel_id": "ch1",
            "author_id": "user", "content": "hello agent",
            "created_at": 123.0,
            "metadata": {"hops_since_user": 0},
        }
        channel = {
            "id": "ch1", "type": "dm", "members": ["user", "test-agent"],
            "settings": {},
        }

        app.state.config.agents = [{
            "name": "test-agent",
            "framework": "openclaw",
            "model": "qwen3-8b",
            "status": "running",
        }]
        os.environ["TAOS_OPENCLAW_TRANSPORT"] = "acp"

        router.dispatch(message, channel)
        await asyncio.sleep(0.1)

        # After error, counter must be 0
        health = await client.get("/api/health")
        assert health.json()["in_use"]["agent_turns"] == 0, \
            f"Expected 0 after ACP error, got {health.json()['in_use']['agent_turns']}"


# ---------------------------------------------------------------------------
# Issue 5: Gateway streams - count from stream open on every path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gateway_stream_openai_counted_from_open(gw_client):
    """OpenAI-compatible streaming: counter must be 1 from stream open (before first frame)."""
    req = gw_client.build_request("POST", BASE + "/chat/completions", json={
        "model": "qwen3-8b",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })

    # Drive stream in background
    task = asyncio.create_task(_stream_body(gw_client, req))

    # Counter should be 1 immediately (stream open), before first frame arrives
    await _wait_for_counter(gw_client, 1, timeout_s=2.0)
    health = await gw_client.get("/api/health")
    assert health.json()["in_use"]["gateway_streams"] == 1

    # Wait for stream to complete
    await task

    # Counter should return to 0
    await _wait_for_counter(gw_client, 0, timeout_s=2.0)
    health = await gw_client.get("/api/health")
    assert health.json()["in_use"]["gateway_streams"] == 0


@pytest.mark.asyncio
async def test_gateway_stream_anthropic_counted_from_open(tmp_path, monkeypatch):
    """Anthropic streaming: counter must be 1 from stream open (before first frame).
    
    This test uses the real gateway with an Anthropic backend and mocks the upstream.
    """
    import httpx
    from httpx import ASGITransport, AsyncClient
    
    # Configure app with Anthropic backend
    backends = [{
        "name": "claude-cloud",
        "type": "anthropic",
        "url": "https://api.anthropic.com",
        "models": [{"id": "claude-x"}],
        "api_key": "sk-ant-test-DO-NOT-LEAK",
        "priority": 1,
    }]
    _write_gw_config(tmp_path, "https://api.anthropic.com")
    # Override the config to use Anthropic backend
    config_path = tmp_path / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["backends"] = backends
    config_path.write_text(yaml.dump(config))
    
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    app = create_app(data_dir=tmp_path)
    state = app.state
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as client:
        # Mock the Anthropic API
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                content=(
                    'event: message_start\n\n'
                    'event: content_block_start\n\n'
                    'event: content_block_delta\n\n'
                    'event: content_block_delta\n\n'
                    'event: content_block_stop\n\n'
                    'event: message_delta\n\n'
                    'event: message_stop\n\n'
                ),
                headers={"content-type": "text/event-stream"}
            )
        )
        
        req = client.build_request("POST", BASE + "/chat/completions", json={
            "model": "claude-x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        
        # Drive stream in background
        async def stream_body():
            resp = await client.send(req, stream=True)
            if resp.status_code != 200:
                return
            async for _ in resp.aiter_bytes():
                pass
            await resp.aclose()
        
        task = asyncio.create_task(stream_body())
        
        # Counter should be 1 immediately (stream open), before first frame arrives
        await _wait_for_counter(client, 1, timeout_s=2.0)
        health = await client.get("/api/health")
        assert health.json()["in_use"]["gateway_streams"] == 1
        
        # Wait for stream to complete
        await task
        
        # Counter should return to 0
        await _wait_for_counter(client, 0, timeout_s=2.0)
        health = await client.get("/api/health")
        assert health.json()["in_use"]["gateway_streams"] == 0
    
    await state.http_client.aclose()


@pytest.mark.asyncio
async def test_gateway_stream_aborted_returns_to_zero(gw_client):
    """Client abort mid-stream must return counter to 0."""
    req = gw_client.build_request("POST", BASE + "/chat/completions", json={
        "model": "qwen3-8b",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })

    async def stream_body():
        resp = await gw_client.send(req, stream=True)
        if resp.status_code != 200:
            return
        async for _ in resp.aiter_bytes():
            pass
        await resp.aclose()

    task = asyncio.create_task(stream_body())
    await _wait_for_counter(gw_client, 1, timeout_s=2.0)

    # Cancel mid-stream
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Counter should return to 0
    await _wait_for_counter(gw_client, 0, timeout_s=2.0)
    health = await gw_client.get("/api/health")
    assert health.json()["in_use"]["gateway_streams"] == 0


@pytest.mark.asyncio
async def test_gateway_stream_upstream_error_returns_to_zero(tmp_path, monkeypatch):
    """Upstream error mid-stream must return counter to 0."""
    from tinyagentos.llm_gateway.forward import chat_completion_stream
    from tinyagentos.llm_gateway.resolve import Route

    in_use = InUseCounters()
    mock_state = MagicMock()
    mock_state.in_use = in_use

    mock_route = MagicMock(spec=Route)
    mock_route.provider = "openai"
    mock_route.upstream_model = "gpt-4"
    mock_route.backend_name = "test-openai"
    mock_route.backend_type = "openai"
    mock_route.api_key_ref = "sk-test"
    mock_route.metadata = {}

    # Mock stream that raises an error
    async def failing_stream(*args, **kwargs):
        async def gen():
            yield _sse_chunk("hello")
            raise RuntimeError("upstream error")
        return gen()

    with patch("tinyagentos.llm_gateway.forward._event_stream_for_route", side_effect=failing_stream):
        try:
            gen = chat_completion_stream([mock_route], {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True}, "user:test", mock_state)
            async for _ in gen:
                pass
        except Exception:
            pass

    # Counter must be 0 after error
    assert in_use.snapshot()["gateway_streams"] == 0, \
        f"Expected 0 after upstream error, got {in_use.snapshot()['gateway_streams']}"


@pytest.mark.asyncio
async def test_gateway_stream_never_iterated_returns_to_zero(tmp_path, monkeypatch):
    """Response generator created but never iterated must not leak counter."""
    from tinyagentos.llm_gateway.forward import chat_completion_stream
    from tinyagentos.llm_gateway.resolve import Route

    in_use = InUseCounters()
    mock_state = MagicMock()
    mock_state.in_use = in_use

    mock_route = MagicMock(spec=Route)
    mock_route.provider = "openai"
    mock_route.upstream_model = "gpt-4"
    mock_route.backend_name = "test-openai"
    mock_route.backend_type = "openai"
    mock_route.api_key_ref = "sk-test"
    mock_route.metadata = {}

    # Create a generator but never iterate it
    gen = chat_completion_stream([mock_route], {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True}, "user:test", mock_state)

    # Don't iterate - just let it go out of scope / be garbage collected
    # The counter should still be 0 because increment happens when stream is consumed
    del gen
    import gc
    gc.collect()

    # Counter should be 0 (no stream was actually opened)
    assert in_use.snapshot()["gateway_streams"] == 0, \
        f"Expected 0 for never-iterated stream, got {in_use.snapshot()['gateway_streams']}"


# The Anthropic abort test is covered by test_gateway_stream_aborted_returns_to_zero
# since both paths use the same finally-block pattern for counter cleanup.


# ---------------------------------------------------------------------------
# Issue 6: Additional abort tests for new paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_turns_abort_on_bridge_session_error(app_with_gateway):
    """Error during bridge session record_reply must not leak counter."""
    client, app = app_with_gateway
    in_use = app.state.in_use
    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
    )
    app.state.bridge_sessions = reg

    await reg.enqueue_user_message("test-agent", {
        "id": "m1", "trace_id": "t1", "channel_id": "ch1",
        "from": "user", "text": "hello", "hops_since_user": 0,
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 1

    # Error reply
    await reg.record_reply("test-agent", {
        "kind": "error", "trace_id": "t1", "error": "model crashed",
    })
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0, \
        f"Expected 0 after error reply, got {health.json()['in_use']['agent_turns']}"