"""Tests for the /api/health in_use counters.

These tests FAIL on origin/dev (no in_use key) and pass once the counters
are wired into the gateway, the bridge registry and the health route.

The gateway streams are observed by driving a real streaming request in a
background task and polling /api/health from a separate connection while the
stream is open: the test client buffers the whole streaming body before the
response object is returned, so the stream cannot be held open on the client
side, but the gateway keeps the counter at 1 for the whole duration.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.app import create_app
from tinyagentos.bridge_session import BridgeSessionRegistry

BASE = "/api/llm/v1"


def _sse_chunk(text: str) -> bytes:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\n".encode()


async def _handle_sse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """A minimal HTTP/1.1 server that drips SSE chunks slowly."""
    await reader.readline()  # request line
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
    writer.write(b"data: [DONE]\n\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


@pytest_asyncio.fixture
async def slow_sse_server():
    """A real local HTTP server that drips SSE chunks with delays."""
    server = await asyncio.start_server(_handle_sse, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


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
async def gw_client(slow_sse_server, tmp_path, monkeypatch):
    """Real app plus signed-in client; the upstream is a slow local SSE server."""
    _write_gw_config(tmp_path, slow_sse_server)
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


@pytest.mark.asyncio
async def test_health_in_use_gateway_streams(gw_client):
    req = gw_client.build_request(
        "POST",
        BASE + "/chat/completions",
        json={
            "model": "qwen3-8b",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    # Drive the stream in the background: the gateway keeps one stream open
    # for a couple of seconds, so in_use.gateway_streams is 1 during that
    # whole window.
    task = asyncio.create_task(_stream_body(gw_client, req))
    await _wait_for_counter(gw_client, 1)
    health = await gw_client.get("/api/health")
    assert health.json()["in_use"]["gateway_streams"] == 1
    await task
    health = await gw_client.get("/api/health")
    assert health.json()["in_use"]["gateway_streams"] == 0


@pytest.mark.asyncio
async def test_health_in_use_gateway_stream_abort(gw_client):
    req = gw_client.build_request(
        "POST",
        BASE + "/chat/completions",
        json={
            "model": "qwen3-8b",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    task = asyncio.create_task(_stream_body(gw_client, req))
    await _wait_for_counter(gw_client, 1)
    # Abort mid-stream by cancelling the in-flight request.
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # Poll until the abort is reflected in /api/health.
    deadline = asyncio.get_event_loop().time() + 5.0
    while True:
        health = await gw_client.get("/api/health")
        if health.json()["in_use"]["gateway_streams"] == 0:
            break
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError("aborted gateway stream did not return to zero")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_health_in_use_agent_turns(client):
    app = client._transport.app
    in_use = getattr(app.state, "in_use", None)
    reg = BridgeSessionRegistry(
        in_use=in_use,
        chat_messages=app.state.chat_messages,
        chat_channels=app.state.chat_channels,
    )
    app.state.bridge_sessions = reg

    # Enqueue a user message for a config agent: one turn in flight.
    await reg.enqueue_user_message(
        "test-agent",
        {
            "id": "m1",
            "trace_id": "t1",
            "channel_id": "ch1",
            "from": "user",
            "text": "hello agent",
            "hops_since_user": 0,
        },
    )
    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["in_use"]["agent_turns"] == 1

    # Record the final reply: the turn ends.
    await reg.record_reply(
        "test-agent",
        {
            "kind": "final",
            "id": "r1",
            "trace_id": "t1",
            "channel_id": "ch1",
            "content": "hello user",
        },
    )
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0

    # Separately: an aborted turn (error reply) also returns to zero.
    await reg.enqueue_user_message(
        "test-agent",
        {
            "id": "m2",
            "trace_id": "t2",
            "channel_id": "ch1",
            "from": "user",
            "text": "abort me",
            "hops_since_user": 0,
        },
    )
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 1

    await reg.record_reply(
        "test-agent", {"kind": "error", "trace_id": "t2", "error": "model crashed"}
    )
    health = await client.get("/api/health")
    assert health.json()["in_use"]["agent_turns"] == 0
