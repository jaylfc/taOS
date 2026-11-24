"""Tests for model scheduler wiring into the live gateway path (tsk-mo6f5o).

RED-FIRST: These tests are written against current dev and MUST FAIL before the fix.
They verify:
1. A chat completion through the real gateway registers the model as resident with last_used_at set
2. In-use count is 1 during the call and 0 after (including error, stream abort)
3. Eviction path called while in-use evicts nothing
"""
from __future__ import annotations

import asyncio
import json
import pytest
import respx
import httpx

from tinyagentos.llm_gateway.forward import chat_completion, _event_stream_for_route
from tinyagentos.llm_gateway.resolve import Route
from tinyagentos.scheduler.core_aware_scheduler import CoreAwareModelScheduler
from tinyagentos.scheduler.loaded_model import LoadedModel, PriorityClass
from tinyagentos.scheduler.resource_shape import make_cpu_shape


def _npu_shape_lookup(_backend: str):
    from tinyagentos.scheduler.resource_shape import make_rk3588_npu_shape
    return make_rk3588_npu_shape()


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