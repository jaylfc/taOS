"""Tests for model scheduler wiring into the live gateway path (tsk-mo6f5o).

RED-FIRST: These tests are written against current dev and MUST FAIL before the fix.
They verify:
1. A chat completion through the real gateway registers the model as resident with last_used_at set
2. In-use count is 1 during the call and 0 after (including error, stream abort)
3. Eviction path called while in-use evicts nothing
4. Anthropic streaming releases in-use count (BLOCKER: tsk-w4f3ki #1)
5. Forward streaming early close releases in-use count (MAJOR: tsk-w4f3ki #2)
6. In-use count keyed by (model, backend) not just model (MAJOR: tsk-w4f3ki #3)
"""
from __future__ import annotations

import asyncio
import json
import pytest
import respx
import httpx

from tinyagentos.llm_gateway.forward import chat_completion, _event_stream_for_route, chat_completion_stream
from tinyagentos.llm_gateway.anthropic import chat_completion_stream_anthropic
from tinyagentos.llm_gateway.resolve import Route
from tinyagentos.scheduler.core_aware_scheduler import CoreAwareModelScheduler
from tinyagentos.scheduler.loaded_model import LoadedModel, PriorityClass
from tinyagentos.scheduler.resource_shape import make_cpu_shape


def _npu_shape_lookup(_backend: str):
    from tinyagentos.scheduler.resource_shape import make_rk3588_npu_shape
    return make_rk3588_npu_shape()


def _cpu_shape_lookup(_backend: str):
    return make_cpu_shape()


def _fake_state(scheduler: CoreAwareModelScheduler) -> "types.SimpleNamespace":
    from types import SimpleNamespace
    return SimpleNamespace(
        model_activity=None,
        trace_registry=None,
        data_dir=None,
        secrets=None,
        model_scheduler=scheduler,
        lifecycle_manager=None,
    )


@pytest.mark.asyncio
async def test_chat_completion_registers_model_in_scheduler():
    """Test 1: A chat completion through the real gateway entry registers
    the model as a resident of app.state.model_scheduler with last_used_at set.
    On dev this fails because app.state.model_scheduler does not exist."""
    scheduler = CoreAwareModelScheduler(shape_lookup=_npu_shape_lookup)
    state = _fake_state(scheduler)

    with respx.mock:
        respx.post("http://test-backend/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
        )

        route = Route(
            model_name="test-model",
            provider="openai",
            upstream_model="test-model",
            api_base="http://test-backend/v1",
            api_key_ref=None,
            backend_name="test-backend",
            backend_type="llama-cpp",
        )

        await chat_completion([route], {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)

    # After the call, the model should be registered in the scheduler
    resident = scheduler.get_resident("test-model")
    assert resident is not None, "Model should be registered in scheduler after chat completion"
    assert resident.model_id == "test-model"
    assert resident.backend == "test-backend"
    assert resident.last_used_at > 0, "last_used_at should be set after request"


@pytest.mark.asyncio
async def test_in_use_count_during_and_after_call():
    """Test 2: In-use count is 1 during the call and 0 after, including
    after an upstream error and after a streamed response abandoned midway."""
    scheduler = CoreAwareModelScheduler(shape_lookup=_npu_shape_lookup)
    state = _fake_state(scheduler)

    # Test non-streaming success
    with respx.mock:
        respx.post("http://test-backend/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
        )

        route = Route(
            model_name="test-model-2",
            provider="openai",
            upstream_model="test-model-2",
            api_base="http://test-backend/v1",
            api_key_ref=None,
            backend_name="test-backend",
            backend_type="llama-cpp",
        )

        # Before call, in_use_count should be 0
        resident_before = scheduler.get_resident("test-model-2")
        if resident_before:
            assert resident_before.in_use_count == 0

        await chat_completion([route], {"model": "test-model-2", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)

    # After success, in_use_count should be 0
    resident_after = scheduler.get_resident("test-model-2")
    assert resident_after is not None
    assert resident_after.in_use_count == 0, "in_use_count should be 0 after successful completion"
    assert resident_after.last_used_at > 0

    # Test upstream error
    with respx.mock:
        respx.post("http://test-backend/v1/chat/completions").mock(
            return_value=httpx.Response(503, json={"error": "boom"})
        )

        route = Route(
            model_name="test-model-3",
            provider="openai",
            upstream_model="test-model-3",
            api_base="http://test-backend/v1",
            api_key_ref=None,
            backend_name="test-backend",
            backend_type="llama-cpp",
        )

        with pytest.raises(Exception):
            await chat_completion([route], {"model": "test-model-3", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)

    # After error, in_use_count should be 0
    resident_after_error = scheduler.get_resident("test-model-3")
    assert resident_after_error is not None
    assert resident_after_error.in_use_count == 0, "in_use_count should be 0 after upstream error"


@pytest.mark.asyncio
async def test_stream_abandoned_midway_in_use_count():
    """Test 2 (stream): In-use count is 1 during streaming and 0 after
    the stream is abandoned midway (client disconnect)."""
    scheduler = CoreAwareModelScheduler(shape_lookup=_npu_shape_lookup)
    state = _fake_state(scheduler)

    sse = (
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
        b"data: [DONE]\n\n"
    )

    with respx.mock:
        respx.post("http://test-backend/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        route = Route(
            model_name="test-model-4",
            provider="openai",
            upstream_model="test-model-4",
            api_base="http://test-backend/v1",
            api_key_ref=None,
            backend_name="test-backend",
            backend_type="llama-cpp",
        )

        gen = _event_stream_for_route(route, {"model": "test-model-4", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)

        # Consume first chunk, then abandon
        first_chunk = await gen.__anext__()
        assert b"hi" in first_chunk

        # Check in_use_count is 1 during the stream
        resident_during = scheduler.get_resident("test-model-4")
        assert resident_during is not None
        assert resident_during.in_use_count == 1, "in_use_count should be 1 during active stream"

        # Abandon the stream (client disconnect)
        await gen.aclose()

    # After abandon, in_use_count should be 0
    resident_after = scheduler.get_resident("test-model-4")
    assert resident_after is not None
    assert resident_after.in_use_count == 0, "in_use_count should be 0 after stream abandoned"


@pytest.mark.asyncio
async def test_eviction_skips_in_use_model():
    """Test 3: The eviction path called while a model is in-use evicts nothing.
    The model with in_use_count > 0 should not be selected as a victim."""
    from tinyagentos.scheduler.core_aware_scheduler import ResourceContention

    # Fill all cores with in-use models - should raise ResourceContention
    scheduler = CoreAwareModelScheduler(shape_lookup=_npu_shape_lookup)
    m1 = LoadedModel("m1", "rkllama", 512, {"cores": [0]}, "0", PriorityClass.INTERACTIVE)
    m1.in_use_count = 1
    m2 = LoadedModel("m2", "rkllama", 512, {"cores": [1]}, "1", PriorityClass.INTERACTIVE)
    m2.in_use_count = 1
    m3 = LoadedModel("m3", "rkllama", 512, {"cores": [2]}, "2", PriorityClass.INTERACTIVE)
    m3.in_use_count = 1
    scheduler.register_loaded(m1)
    scheduler.register_loaded(m2)
    scheduler.register_loaded(m3)

    with pytest.raises(ResourceContention):
        await scheduler.load_with_core_awareness(
            model_id="new-model",
            backend_name="rkllama",
            priority=PriorityClass.INTERACTIVE,
        )

    # All in-use models should still be there
    assert scheduler.get_resident("m1") is not None
    assert scheduler.get_resident("m2") is not None
    assert scheduler.get_resident("m3") is not None


@pytest.mark.asyncio
async def test_scheduler_error_does_not_fail_request():
    """Scheduler errors (ResourceContention, etc.) must never fail or delay
    the request - wrapped like _notify_lifecycle does."""
    scheduler = CoreAwareModelScheduler(shape_lookup=_npu_shape_lookup)
    state = _fake_state(scheduler)

    # Make the scheduler raise an error on begin_use
    def failing_begin_use(*args, **kwargs):
        raise RuntimeError("scheduler error")
    scheduler.begin_use = failing_begin_use

    with respx.mock:
        respx.post("http://test-backend/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
        )

        route = Route(
            model_name="test-model-5",
            provider="openai",
            upstream_model="test-model-5",
            api_base="http://test-backend/v1",
            api_key_ref=None,
            backend_name="test-backend",
            backend_type="llama-cpp",
        )

        # The request should succeed despite scheduler error
        result = await chat_completion([route], {"model": "test-model-5", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)
        assert result["choices"][0]["message"]["content"] == "hi"


# =============================================================================
# New tests for tsk-w4f3ki defects
# =============================================================================

async def _consume_anthropic_stream(gen, scheduler, model_name, state):
    """Helper to consume an Anthropic stream and check in_use_count at each stage."""
    # First chunk (role)
    chunk1 = await gen.__anext__()
    # Second chunk (content start or first text)
    chunk2 = await gen.__anext__()
    # Check mid-stream
    resident_during = scheduler.get_resident(model_name)
    assert resident_during is not None, f"Model {model_name} should be registered during stream"
    assert resident_during.in_use_count == 1, f"in_use_count should be 1 during active Anthropic stream for {model_name}"
    # Consume rest
    async for _ in gen:
        pass
    # After completion
    resident_after = scheduler.get_resident(model_name)
    assert resident_after.in_use_count == 0, f"in_use_count should be 0 after normal Anthropic stream completion for {model_name}"


@pytest.mark.asyncio
async def test_anthropic_streaming_releases_in_use_count():
    """BLOCKER (tsk-w4f3ki #1): Anthropic streaming calls begin_use but never
    end_use. In-use count should be 1 mid-stream, 0 after normal completion.
    """
    scheduler = CoreAwareModelScheduler(shape_lookup=_cpu_shape_lookup)
    state = _fake_state(scheduler)

    # Mock Anthropic SSE stream
    anthropic_sse = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"claude-3","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10}}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":"Hello"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
        b'event: content_block_stop\n'
        b'data: {"type":"content_block_stop","index":0}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}\n\n'
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n'
    )

    with respx.mock:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, content=anthropic_sse)
        )

        routes = [
            Route(
                model_name="claude-3-opus",
                provider="anthropic",
                upstream_model="claude-3-opus-20240229",
                api_base="https://api.anthropic.com",
                api_key_ref="os.environ/ANTHROPIC_API_KEY",
                backend_name="anthropic-main",
                backend_type="anthropic",
            )
        ]

        body = {"model": "claude-3-opus", "messages": [{"role": "user", "content": "hi"}]}
        gen = chat_completion_stream_anthropic(routes, body, "user:test", state)

        await _consume_anthropic_stream(gen, scheduler, "claude-3-opus", state)


@pytest.mark.asyncio
async def test_anthropic_streaming_releases_in_use_count_on_abort():
    """BLOCKER (tsk-w4f3ki #1): Anthropic streaming in-use count released
    when generator is closed midway (client disconnect).
    """
    scheduler = CoreAwareModelScheduler(shape_lookup=_cpu_shape_lookup)
    state = _fake_state(scheduler)

    anthropic_sse = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"claude-3","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10}}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":"Hello"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
        # Stream continues but we'll abort
    )

    with respx.mock:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, content=anthropic_sse)
        )

        routes = [
            Route(
                model_name="claude-3-opus-2",
                provider="anthropic",
                upstream_model="claude-3-opus-20240229",
                api_base="https://api.anthropic.com",
                api_key_ref="os.environ/ANTHROPIC_API_KEY",
                backend_name="anthropic-main",
                backend_type="anthropic",
            )
        ]

        body = {"model": "claude-3-opus-2", "messages": [{"role": "user", "content": "hi"}]}
        gen = chat_completion_stream_anthropic(routes, body, "user:test", state)

        # Consume first two chunks (role + content start)
        await gen.__anext__()
        await gen.__anext__()

        # Mid-stream: in_use_count should be 1
        resident_during = scheduler.get_resident("claude-3-opus-2")
        assert resident_during.in_use_count == 1

        # Abandon the stream (client disconnect)
        await gen.aclose()

    # After abort: in_use_count should be 0
    resident_after = scheduler.get_resident("claude-3-opus-2")
    assert resident_after.in_use_count == 0, "in_use_count should be 0 after Anthropic stream abort"


@pytest.mark.asyncio
async def test_anthropic_streaming_releases_in_use_count_on_error():
    """BLOCKER (tsk-w4f3ki #1): Anthropic streaming in-use count released
    when upstream returns an error.
    """
    scheduler = CoreAwareModelScheduler(shape_lookup=_cpu_shape_lookup)
    state = _fake_state(scheduler)

    with respx.mock:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Internal server error", "type": "api_error"}})
        )

        routes = [
            Route(
                model_name="claude-3-opus-3",
                provider="anthropic",
                upstream_model="claude-3-opus-20240229",
                api_base="https://api.anthropic.com",
                api_key_ref="os.environ/ANTHROPIC_API_KEY",
                backend_name="anthropic-main",
                backend_type="anthropic",
            )
        ]

        body = {"model": "claude-3-opus-3", "messages": [{"role": "user", "content": "hi"}]}
        gen = chat_completion_stream_anthropic(routes, body, "user:test", state)

        # Should raise on first iteration
        with pytest.raises(Exception):
            await gen.__anext__()

    # After error: in_use_count should be 0
    resident_after = scheduler.get_resident("claude-3-opus-3")
    assert resident_after is not None
    assert resident_after.in_use_count == 0, "in_use_count should be 0 after Anthropic stream error"


@pytest.mark.asyncio
async def test_forward_streaming_early_close_releases_in_use_count():
    """MAJOR (tsk-w4f3ki #2): _event_stream_for_route calls begin_use
    outside the inner generator. If the outer generator is closed before
    first iteration, _gen never starts and end_use never runs.
    
    With the fix, begin_use is inside _gen, so closing before first iteration
    means begin_use never runs and the model is never registered.
    """
    scheduler = CoreAwareModelScheduler(shape_lookup=_cpu_shape_lookup)
    state = _fake_state(scheduler)

    sse = (
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
        b"data: [DONE]\n\n"
    )

    with respx.mock:
        respx.post("http://test-backend/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        route = Route(
            model_name="test-model-early-close",
            provider="openai",
            upstream_model="test-model-early-close",
            api_base="http://test-backend/v1",
            api_key_ref=None,
            backend_name="test-backend",
            backend_type="llama-cpp",
        )

        # Get the generator but DON'T iterate - close immediately
        gen = _event_stream_for_route(route, {"model": "test-model-early-close", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)

        # Close before any iteration
        await gen.aclose()

    # After early close: model should not be registered at all (begin_use never ran)
    resident_after = scheduler.get_resident("test-model-early-close")
    assert resident_after is None, "Model should not be registered when generator closed before first iteration"


@pytest.mark.asyncio
async def test_chat_completion_stream_fallback_releases_in_use_count():
    """MAJOR (tsk-w4f3ki #2): chat_completion_stream with fallback from
    failing first route to second route should not leak in-use count.
    """
    scheduler = CoreAwareModelScheduler(shape_lookup=_cpu_shape_lookup)
    state = _fake_state(scheduler)

    # First route fails with 503, second succeeds
    with respx.mock:
        respx.post("http://backend1/v1/chat/completions").mock(
            return_value=httpx.Response(503, json={"error": "unavailable"})
        )
        respx.post("http://backend2/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=(
                b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'
                b"data: [DONE]\n\n"
            ))
        )

        routes = [
            Route(
                model_name="fallback-model",
                provider="openai",
                upstream_model="fallback-model",
                api_base="http://backend1/v1",
                api_key_ref=None,
                backend_name="backend1",
                backend_type="llama-cpp",
            ),
            Route(
                model_name="fallback-model",
                provider="openai",
                upstream_model="fallback-model",
                api_base="http://backend2/v1",
                api_key_ref=None,
                backend_name="backend2",
                backend_type="llama-cpp",
            ),
        ]

        from fastapi.responses import StreamingResponse
        resp = await chat_completion_stream(routes, {"model": "fallback-model", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)
        assert isinstance(resp, StreamingResponse)

        # Consume the stream
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        assert any(b"hi" in c for c in chunks)

    # After fallback completion: in_use_count should be 0 for both backends
    resident = scheduler.get_resident("fallback-model")
    assert resident.in_use_count == 0, "in_use_count should be 0 after fallback stream"


@pytest.mark.asyncio
async def test_in_use_count_keyed_by_model_and_backend():
    """MAJOR (tsk-w4f3ki #3): The use count is keyed by model name only.
    end_use(model_id) cannot tell two backends serving the same model name apart.
    begin_use and end_use should be keyed by (route.model_name, route.backend_name)
    per attempt, as forward.py does.
    """
    scheduler = CoreAwareModelScheduler(shape_lookup=_cpu_shape_lookup)
    state = _fake_state(scheduler)

    with respx.mock:
        respx.post("http://backend1/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
        )
        respx.post("http://backend2/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
        )

        # Two different backends serving the same model name
        route1 = Route(
            model_name="shared-model",
            provider="openai",
            upstream_model="shared-model",
            api_base="http://backend1/v1",
            api_key_ref=None,
            backend_name="backend1",
            backend_type="llama-cpp",
        )
        route2 = Route(
            model_name="shared-model",
            provider="openai",
            upstream_model="shared-model",
            api_base="http://backend2/v1",
            api_key_ref=None,
            backend_name="backend2",
            backend_type="llama-cpp",
        )

        # Call first backend
        await chat_completion([route1], {"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)

        # Call second backend
        await chat_completion([route2], {"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]}, "user:test", state)

    # With the current bug, both calls use "shared-model" as the key
    # and the second call's end_use would decrement the same counter
    # The fix should track them separately by (model, backend)
    resident = scheduler.get_resident("shared-model")
    assert resident.in_use_count == 0, "in_use_count should be 0 after two different backends"


@pytest.mark.asyncio
async def test_anthropic_streaming_backend_specific_in_use_count():
    """MAJOR (tsk-w4f3ki #3): Anthropic functions use body["model"] with
    anthropic_routes[0].backend_name while fallback may use another route.
    In-use count should be tracked per (model, backend) pair.
    """
    scheduler = CoreAwareModelScheduler(shape_lookup=_cpu_shape_lookup)
    state = _fake_state(scheduler)

    anthropic_sse = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"claude-3","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10}}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":"Hello"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
        b'event: content_block_stop\n'
        b'data: {"type":"content_block_stop","index":0}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}\n\n'
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n'
    )

    with respx.mock:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, content=anthropic_sse)
        )

        # Two Anthropic backends for the same model
        routes = [
            Route(
                model_name="claude-3-sonnet",
                provider="anthropic",
                upstream_model="claude-3-sonnet-20240229",
                api_base="https://api.anthropic.com",
                api_key_ref="os.environ/ANTHROPIC_API_KEY",
                backend_name="anthropic-primary",
                backend_type="anthropic",
            ),
            Route(
                model_name="claude-3-sonnet",
                provider="anthropic",
                upstream_model="claude-3-sonnet-20240229",
                api_base="https://api.anthropic.com",
                api_key_ref="os.environ/ANTHROPIC_API_KEY",
                backend_name="anthropic-fallback",
                backend_type="anthropic",
            ),
        ]

        body = {"model": "claude-3-sonnet", "messages": [{"role": "user", "content": "hi"}]}
        gen = chat_completion_stream_anthropic(routes, body, "user:test", state)

        # Consume first two chunks (role + content start)
        await gen.__anext__()
        await gen.__anext__()

        # Mid-stream: in_use_count should be 1 for the primary backend
        resident_during = scheduler.get_resident("claude-3-sonnet")
        assert resident_during is not None
        assert resident_during.in_use_count == 1

        # Complete the stream
        async for _ in gen:
            pass

    # After completion: in_use_count should be 0
    resident_after = scheduler.get_resident("claude-3-sonnet")
    assert resident_after.in_use_count == 0, "in_use_count should be 0 after Anthropic stream completion"