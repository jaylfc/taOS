"""The scheduler in-use count through the real gateway route (tsk-mo6f5o).

One rule: a model is "in use" exactly while a request for it is in flight. So
after EVERY way a request can end, the count is back to 0, and a failed or
abandoned request never costs the caller money it was not charged on dev.

Every test drives the real app (``create_app``, TAOS_LLM_GATEWAY=1) through
``POST /api/llm/v1/chat/completions`` and mocks only the upstream with respx.
"""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import respx
import yaml
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.app import create_app
from tinyagentos.llm_gateway import forward
from tinyagentos.llm_gateway.errors import GatewayError
from tinyagentos.scheduler.core_aware_scheduler import CoreAwareModelScheduler
from tinyagentos.scheduler.resource_shape import get_default_shape

BASE = "/api/llm/v1"
_ASYNC = pytest.mark.asyncio(loop_scope="module")

OAI_A = "http://llm-a.test:8080/v1"
OAI_B = "http://llm-b.test:8080/v1"
ANT_A = "https://ant-a.test"
ANT_B = "https://ant-b.test"

BACKENDS = [
    {"name": "oai-a", "type": "openai-compatible", "url": OAI_A,
     "models": [{"id": "qwen3-8b"}], "api_key": "sk-a", "priority": 1},
    {"name": "oai-b", "type": "openai-compatible", "url": OAI_B,
     "models": [{"id": "qwen3-8b"}], "api_key": "sk-b", "priority": 2},
    {"name": "ant-a", "type": "anthropic", "url": ANT_A,
     "models": [{"id": "claude-x"}], "api_key": "sk-ant-a", "priority": 1},
    {"name": "ant-b", "type": "anthropic", "url": ANT_B,
     "models": [{"id": "claude-x"}], "api_key": "sk-ant-b", "priority": 2},
]


def _completion() -> dict:
    return {
        "id": "c1", "object": "chat.completion", "created": 1, "model": "qwen3-8b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


OAI_SSE = (
    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
    b"data: [DONE]\n\n"
)


def _ev(kind: str, **payload) -> bytes:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n".encode()


ANT_HEAD = (
    _ev("message_start", message={
        "id": "m1", "type": "message", "role": "assistant", "content": [],
        "model": "claude-x", "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1}})
    + _ev("content_block_start", index=0, content_block={"type": "text", "text": ""})
    + _ev("content_block_delta", index=0, delta={"type": "text_delta", "text": "Hello"})
)
ANT_TAIL = (
    _ev("content_block_stop", index=0)
    + _ev("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 5})
    + _ev("message_stop")
)
ANT_SSE = ANT_HEAD + ANT_TAIL


def _ant_message() -> dict:
    return {
        "id": "m1", "type": "message", "role": "assistant", "model": "claude-x",
        "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
        "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 1},
    }


class _BrokenStream(httpx.AsyncByteStream):
    """A 200 whose body dies part way: the upstream drops the connection."""

    def __init__(self, head: bytes):
        self._head = head

    async def __aiter__(self):
        yield self._head
        raise httpx.ReadError("connection dropped")

    async def aclose(self):
        return None


def _chat(model: str, stream: bool = False) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}], "stream": stream}


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def gateway(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("in_use_exits")
    (data_dir / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [{"name": "test-backend", "type": "rkllama",
                      "url": "http://localhost:8080", "priority": 1}],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [], "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (data_dir / ".setup_complete").touch()
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        cookies={"taos_session": session}, event_hooks=csrf_event_hooks(),
    ) as c:
        yield SimpleNamespace(app=app, client=c)
    for store in stores:
        await store.close()
    await state.http_client.aclose()


@pytest_asyncio.fixture(loop_scope="module")
async def gw(gateway, monkeypatch):
    """Fresh scheduler, backends, cooldowns and spend/trace spies per test."""
    state = gateway.app.state
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    state.config.backends = copy.deepcopy(BACKENDS)
    state.model_scheduler = CoreAwareModelScheduler(shape_lookup=get_default_shape)
    forward._clear_cooldowns()
    await state.desktop_settings.save_preference("user", "taos_agent", {"model": "qwen3-8b"})
    spend, traces = [], []
    monkeypatch.setattr(forward, "_record_spend", lambda st, principal, usd: spend.append(usd))

    async def _trace(*a, **kw):
        traces.append(a)
    monkeypatch.setattr(forward, "_record_trace", _trace)
    gateway.spend, gateway.traces = spend, traces
    gateway.scheduler = state.model_scheduler
    with respx.mock(assert_all_called=False) as router:
        gateway.upstream = router
        yield gateway
    forward._clear_cooldowns()


def in_use(gw) -> dict:
    """{(model, backend): count} for every resident that is in use."""
    return {(m.model_id, m.backend): m.in_use_count
            for m in gw.scheduler.residents() if m.in_use_count}


def assert_idle(gw) -> None:
    assert in_use(gw) == {}, f"in-use count leaked: {in_use(gw)}"


async def _raw_post(gw, body: dict, *, disconnect_after_body_chunks: int | None):
    """POST through the app as a raw ASGI call so a client disconnect can be
    simulated: after N body chunks the ``receive`` side reports
    ``http.disconnect`` (ASGITransport cannot do that)."""
    c = gw.client
    req = c.build_request("POST", BASE + "/chat/completions", json=body)
    for hook in c.event_hooks["request"]:
        await hook(req)
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": BASE + "/chat/completions", "raw_path": (BASE + "/chat/completions").encode(),
        "query_string": b"", "headers": list(req.headers.raw),
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    payload = req.content
    sent_body = asyncio.Event()
    chunks = []
    state = {"first": True}

    async def receive():
        if state["first"]:
            state["first"] = False
            return {"type": "http.request", "body": payload, "more_body": False}
        await sent_body.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.body" and msg.get("body"):
            chunks.append(msg["body"])
            if (disconnect_after_body_chunks is not None
                    and len(chunks) >= disconnect_after_body_chunks):
                sent_body.set()

    await asyncio.wait_for(gw.app(scope, receive, send), timeout=20)
    return chunks


# --------------------------------------------------------------------------
# OpenAI-compatible, non-streaming
# --------------------------------------------------------------------------

@_ASYNC
async def test_oai_nonstream_in_use_only_while_in_flight(gw):
    seen = {}

    def upstream(request):
        seen.update(in_use(gw))
        return httpx.Response(200, json=_completion())

    gw.upstream.post(f"{OAI_A}/chat/completions").mock(side_effect=upstream)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b"))
    assert resp.status_code == 200, resp.text
    assert seen == {("qwen3-8b", "oai-a"): 1}
    assert_idle(gw)


@_ASYNC
async def test_oai_nonstream_error_in_tracing_releases(gw, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("trace store down")
    monkeypatch.setattr(forward, "_record_trace", boom)
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion()))
    with pytest.raises(RuntimeError):
        await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b"))
    assert_idle(gw)


@_ASYNC
async def test_oai_nonstream_cancelled_during_upstream_call_releases(gw):
    hit = asyncio.Event()

    async def hang(request):
        hit.set()
        await asyncio.Event().wait()

    gw.upstream.post(f"{OAI_A}/chat/completions").mock(side_effect=hang)
    task = asyncio.create_task(gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b")))
    await asyncio.wait_for(hit.wait(), 10)
    assert in_use(gw) == {("qwen3-8b", "oai-a"): 1}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_idle(gw)


@_ASYNC
async def test_oai_nonstream_failover_moves_count_to_serving_backend(gw):
    seen = {}
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(return_value=httpx.Response(503))

    def second(request):
        seen.update(in_use(gw))
        return httpx.Response(200, json=_completion())

    gw.upstream.post(f"{OAI_B}/chat/completions").mock(side_effect=second)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b"))
    assert resp.status_code == 200, resp.text
    assert seen == {("qwen3-8b", "oai-b"): 1}
    assert_idle(gw)


@_ASYNC
async def test_two_aliases_of_one_model_share_one_resident(gw):
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion()))
    for alias in ("qwen3-8b", "taos-default"):
        resp = await gw.client.post(BASE + "/chat/completions", json=_chat(alias))
        assert resp.status_code == 200, resp.text
    residents = gw.scheduler.residents()
    assert [(m.model_id, m.backend) for m in residents] == [("qwen3-8b", "oai-a")]
    assert_idle(gw)


# --------------------------------------------------------------------------
# OpenAI-compatible, streaming
# --------------------------------------------------------------------------

@_ASYNC
async def test_oai_stream_success(gw):
    seen = {}

    def upstream(request):
        seen.update(in_use(gw))
        return httpx.Response(200, content=OAI_SSE)

    gw.upstream.post(f"{OAI_A}/chat/completions").mock(side_effect=upstream)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b", True))
    assert resp.status_code == 200, resp.text
    assert seen == {("qwen3-8b", "oai-a"): 1}
    assert_idle(gw)


@_ASYNC
async def test_oai_stream_key_resolution_failure_releases(gw, monkeypatch):
    async def no_key(state, ref):
        raise GatewayError(502, "key store unavailable")
    monkeypatch.setattr(forward, "resolve_api_key", no_key)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b", True))
    assert resp.status_code >= 400
    assert_idle(gw)


@_ASYNC
async def test_oai_stream_pre_stream_http_error_releases(gw):
    for base in (OAI_A, OAI_B):
        gw.upstream.post(f"{base}/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b", True))
    assert resp.status_code >= 400
    assert_idle(gw)
    assert gw.spend == []


@_ASYNC
async def test_oai_stream_failover_moves_count(gw):
    seen = {}
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(return_value=httpx.Response(503))

    def second(request):
        seen.update(in_use(gw))
        return httpx.Response(200, content=OAI_SSE)

    gw.upstream.post(f"{OAI_B}/chat/completions").mock(side_effect=second)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b", True))
    assert resp.status_code == 200, resp.text
    assert seen == {("qwen3-8b", "oai-b"): 1}
    assert_idle(gw)


@_ASYNC
async def test_oai_stream_mid_stream_upstream_error_releases(gw):
    head = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(
        return_value=httpx.Response(200, stream=_BrokenStream(head)))
    try:
        await gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b", True))
    except Exception:  # noqa: BLE001 - the abort itself is not what is asserted
        pass
    assert_idle(gw)


@_ASYNC
async def test_oai_stream_client_disconnect_releases(gw):
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(
        return_value=httpx.Response(200, content=OAI_SSE))
    await _raw_post(gw, _chat("qwen3-8b", True), disconnect_after_body_chunks=1)
    assert_idle(gw)


@_ASYNC
async def test_oai_stream_cancelled_during_upstream_call_releases(gw):
    hit = asyncio.Event()

    async def hang(request):
        hit.set()
        await asyncio.Event().wait()

    gw.upstream.post(f"{OAI_A}/chat/completions").mock(side_effect=hang)
    task = asyncio.create_task(
        gw.client.post(BASE + "/chat/completions", json=_chat("qwen3-8b", True)))
    await asyncio.wait_for(hit.wait(), 10)
    assert in_use(gw) == {("qwen3-8b", "oai-a"): 1}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_idle(gw)


@_ASYNC
async def test_oai_stream_closed_early_by_consumer_releases(gw):
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(
        return_value=httpx.Response(200, content=OAI_SSE))
    from tinyagentos.llm_gateway.resolve import find_routes, routing_table
    routes = find_routes(await routing_table(gw.app.state), "qwen3-8b")[:1]
    resp = await forward.chat_completion_stream(
        routes, _chat("qwen3-8b", True), "user:t", gw.app.state)
    it = resp.body_iterator
    await it.__anext__()
    assert in_use(gw) == {("qwen3-8b", "oai-a"): 1}
    await it.aclose()
    assert_idle(gw)


@_ASYNC
async def test_oai_stream_response_dropped_before_first_read_releases(gw):
    gw.upstream.post(f"{OAI_A}/chat/completions").mock(
        return_value=httpx.Response(200, content=OAI_SSE))
    from tinyagentos.llm_gateway.resolve import find_routes, routing_table
    routes = find_routes(await routing_table(gw.app.state), "qwen3-8b")[:1]
    resp = await forward.chat_completion_stream(
        routes, _chat("qwen3-8b", True), "user:t", gw.app.state)
    # The server drops the response before streaming a byte: it only runs
    # the response's background task.
    await resp.body_iterator.aclose()
    await resp.background()
    assert_idle(gw)


# --------------------------------------------------------------------------
# Anthropic, non-streaming
# --------------------------------------------------------------------------

@_ASYNC
async def test_ant_nonstream_in_use_only_while_in_flight(gw):
    seen = {}

    def upstream(request):
        seen.update(in_use(gw))
        return httpx.Response(200, json=_ant_message())

    gw.upstream.post(f"{ANT_A}/v1/messages").mock(side_effect=upstream)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x"))
    assert resp.status_code == 200, resp.text
    assert seen == {("claude-x", "ant-a"): 1}
    assert_idle(gw)


@_ASYNC
async def test_ant_nonstream_failover_moves_count(gw):
    seen = {}
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(return_value=httpx.Response(500))

    def second(request):
        seen.update(in_use(gw))
        return httpx.Response(200, json=_ant_message())

    gw.upstream.post(f"{ANT_B}/v1/messages").mock(side_effect=second)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x"))
    assert resp.status_code == 200, resp.text
    assert seen == {("claude-x", "ant-b"): 1}
    assert_idle(gw)


@_ASYNC
async def test_ant_nonstream_key_resolution_failure_releases(gw, monkeypatch):
    async def no_key(state, ref):
        raise GatewayError(502, "key store unavailable")
    monkeypatch.setattr(forward, "resolve_api_key", no_key)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x"))
    assert resp.status_code >= 400
    assert_idle(gw)


@_ASYNC
async def test_ant_nonstream_error_in_tracing_releases(gw, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("trace store down")
    monkeypatch.setattr(forward, "_record_trace", boom)
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(
        return_value=httpx.Response(200, json=_ant_message()))
    with pytest.raises(RuntimeError):
        await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x"))
    assert_idle(gw)


# --------------------------------------------------------------------------
# Anthropic, streaming
# --------------------------------------------------------------------------

@_ASYNC
async def test_ant_stream_success_keyed_by_route_model(gw):
    seen = {}

    def upstream(request):
        seen.update(in_use(gw))
        return httpx.Response(200, content=ANT_SSE)

    gw.upstream.post(f"{ANT_A}/v1/messages").mock(side_effect=upstream)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x", True))
    assert resp.status_code == 200, resp.text
    assert seen == {("claude-x", "ant-a"): 1}
    assert_idle(gw)
    assert len(gw.spend) <= 1  # settled once


@_ASYNC
async def test_ant_stream_two_aliases_share_one_resident(gw):
    await gw.app.state.desktop_settings.save_preference(
        "user", "taos_agent", {"model": "claude-x"})
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(
        return_value=httpx.Response(200, content=ANT_SSE))
    for alias in ("claude-x", "taos-default"):
        resp = await gw.client.post(BASE + "/chat/completions", json=_chat(alias, True))
        assert resp.status_code == 200, resp.text
    assert [(m.model_id, m.backend) for m in gw.scheduler.residents()] == [("claude-x", "ant-a")]
    assert_idle(gw)


@_ASYNC
async def test_ant_stream_key_resolution_failure_releases(gw, monkeypatch):
    async def no_key(state, ref):
        raise GatewayError(502, "key store unavailable")
    monkeypatch.setattr(forward, "resolve_api_key", no_key)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x", True))
    assert resp.status_code >= 400
    assert_idle(gw)
    assert gw.spend == [] and gw.traces == []


@_ASYNC
async def test_ant_stream_pre_stream_http_error_releases_and_charges_nothing(gw):
    """Dev records no trace and no spend for an upstream 401 before any byte
    streamed; settling there was a regression."""
    for base in (ANT_A, ANT_B):
        gw.upstream.post(f"{base}/v1/messages").mock(
            return_value=httpx.Response(401, json={"error": {"type": "authentication_error",
                                                             "message": "bad key"}}))
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x", True))
    assert resp.status_code >= 400
    assert_idle(gw)
    assert gw.spend == []
    assert gw.traces == []


@_ASYNC
async def test_ant_stream_failover_charges_nothing_for_failed_attempt(gw):
    seen = {}
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(return_value=httpx.Response(500))

    def second(request):
        seen.update(in_use(gw))
        return httpx.Response(200, content=ANT_SSE)

    gw.upstream.post(f"{ANT_B}/v1/messages").mock(side_effect=second)
    resp = await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x", True))
    assert resp.status_code == 200, resp.text
    assert seen == {("claude-x", "ant-b"): 1}
    assert_idle(gw)
    assert len(gw.traces) == 1, "only the attempt that streamed is recorded"


@_ASYNC
async def test_ant_stream_mid_stream_upstream_error_releases_and_settles_once(gw):
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(
        return_value=httpx.Response(200, stream=_BrokenStream(ANT_HEAD)))
    try:
        await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x", True))
    except Exception:  # noqa: BLE001
        pass
    assert_idle(gw)
    assert len(gw.traces) == 1, "a stream that started is settled exactly once"


@_ASYNC
async def test_ant_stream_client_disconnect_releases_and_still_charges(gw):
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(
        return_value=httpx.Response(200, content=ANT_SSE))
    await _raw_post(gw, _chat("claude-x", True), disconnect_after_body_chunks=1)
    assert_idle(gw)
    assert len(gw.traces) == 1


@_ASYNC
async def test_ant_stream_cancelled_during_upstream_call_releases(gw):
    hit = asyncio.Event()

    async def hang(request):
        hit.set()
        await asyncio.Event().wait()

    gw.upstream.post(f"{ANT_A}/v1/messages").mock(side_effect=hang)
    task = asyncio.create_task(
        gw.client.post(BASE + "/chat/completions", json=_chat("claude-x", True)))
    await asyncio.wait_for(hit.wait(), 10)
    assert in_use(gw) == {("claude-x", "ant-a"): 1}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_idle(gw)
    assert gw.spend == [] and gw.traces == []


@_ASYNC
async def test_ant_stream_error_in_tracing_releases(gw, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("trace store down")
    monkeypatch.setattr(forward, "_record_trace", boom)
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(
        return_value=httpx.Response(200, content=ANT_SSE))
    try:
        await gw.client.post(BASE + "/chat/completions", json=_chat("claude-x", True))
    except Exception:  # noqa: BLE001
        pass
    assert_idle(gw)


@_ASYNC
async def test_ant_stream_closed_early_by_consumer_releases(gw):
    gw.upstream.post(f"{ANT_A}/v1/messages").mock(
        return_value=httpx.Response(200, content=ANT_SSE))
    from tinyagentos.llm_gateway.anthropic import chat_completion_stream_anthropic
    from tinyagentos.llm_gateway.resolve import find_routes, routing_table
    routes = find_routes(await routing_table(gw.app.state), "claude-x")
    gen = chat_completion_stream_anthropic(routes, _chat("claude-x", True), "user:t", gw.app.state)
    await gen.__anext__()
    assert in_use(gw) == {("claude-x", "ant-a"): 1}
    await gen.aclose()
    assert_idle(gw)
    assert len(gw.traces) == 1


# --------------------------------------------------------------------------
# The scheduler and its shape lookup
# --------------------------------------------------------------------------

def test_begin_use_looks_the_shape_up_by_backend_type_not_backend_name():
    """app.py hands the scheduler get_default_shape, which keys on the backend
    TYPE. A backend named "my-npu" of type rkllama must get the NPU shape."""
    sched = CoreAwareModelScheduler(shape_lookup=get_default_shape)
    resident = sched.begin_use("m", "my-npu", "rkllama")
    assert resident.tp_mode, "an rkllama backend is registered with its cores"
    plain = sched.begin_use("m2", "my-cpu", "llama-cpp")
    assert not plain.tp_mode


def test_failed_begin_use_never_ends_another_requests_use():
    class Exploding:
        def begin_use(self, *a):
            raise RuntimeError("scheduler broken")

        def end_use(self, *a):  # pragma: no cover - must not be called
            raise AssertionError("end_use without a begin_use")

    route = SimpleNamespace(model_name="m", backend_name="b", backend_type="t")
    with forward._SchedulerInUse(SimpleNamespace(model_scheduler=Exploding()), route):
        pass
