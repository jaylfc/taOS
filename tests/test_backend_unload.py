"""Residency backend unload adapters (#3230 slice B1 interface; llama-swap #3229).

Replies mirror llama-swap v261 as observed 2026-10-02 against a source build
(commit 42d8a5d): "OK" 200 for a known model whether or not it was running
(aliases resolve), a JSON 404 for an unknown one.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from tinyagentos.backend_unload import UNLOAD_CAPABLE_TYPES, unload_capable, unload_model

_NOT_FOUND = {"src": "llama-swap", "error": {"message": "model not found",
              "type": "invalid_request_error", "param": None, "code": "not_found"}}


def _llama_swap(seen: list, known=("qwen-chat", "qwen", "org/embed-model")):
    prefix = "/api/models/unload/"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode()))
        path = request.url.path
        if request.method == "POST" and path == "/api/models/unload":  # unload ALL
            return httpx.Response(200, json={"msg": "ok"})
        if request.method == "POST" and path.startswith(prefix) and path[len(prefix):] in known:
            return httpx.Response(200, text="OK")
        return httpx.Response(404, json=_NOT_FOUND)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _unload(client, model, backend_type="llama-swap", base_url="http://127.0.0.1:8080/"):
    return await unload_model(client, backend_type=backend_type, base_url=base_url, model=model)


def _ollama(seen: list, known=("qwen2.5:7b",)):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/api/generate" and body.get("model") in known:
            return httpx.Response(200, json={"model": body["model"], "done": True,
                                             "done_reason": "unload"})
        return httpx.Response(404, json={"error": "model not found"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _rkllama(seen: list, known=("qwen-rk",)):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/unload_model" and body.get("model") in known:
            return httpx.Response(200, json={"status": "unloaded"})
        return httpx.Response(404, json={"error": "model not loaded"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_llama_swap_unloads_one_model_by_name():
    seen: list = []
    async with _llama_swap(seen) as client:
        assert await _unload(client, "qwen-chat") is True
        assert await _unload(client, "org/embed-model") is True
    assert seen == [("POST", "/api/models/unload/qwen-chat"),
                    ("POST", "/api/models/unload/org%2Fembed-model")]


@pytest.mark.asyncio
@pytest.mark.guards("tinyagentos.backend_unload:unload_model",
                    replace=[("if not resp.is_success:", "if False:")])
async def test_llama_swap_unknown_model_is_a_failed_unload():
    seen: list = []
    async with _llama_swap(seen) as client:
        assert await _unload(client, "nope") is False
    # Only the one model: never the unload-all routes (POST /api/models/unload, GET /unload).
    assert seen == [("POST", "/api/models/unload/nope")]


@pytest.mark.asyncio
async def test_connection_error_returns_false():
    def refuse(request):
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        assert await _unload(client, "qwen-chat") is False


@pytest.mark.asyncio
@pytest.mark.guards("tinyagentos.backend_unload:unload_model",
                    replace=[("_UNLOADERS.get(backend_type)",
                              "_UNLOADERS.get(backend_type, _unload_llama_swap)")])
async def test_unregistered_type_returns_false_without_request():
    seen: list = []
    async with _llama_swap(seen) as client:
        for backend_type in ("llama-cpp", "vllm", "openai"):
            assert await _unload(client, "qwen-chat", backend_type=backend_type) is False
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.guards("tinyagentos.backend_unload:unload_model",
                    replace=[('if model in ("", ".", ".."):', "if False:")])
@pytest.mark.parametrize("model", ["", ".", ".."])
async def test_dot_and_empty_names_refused_without_request(model):
    # "." would be normalized away and reach llama-swap's unload-all route.
    seen: list = []
    async with _llama_swap(seen) as client:
        assert await _unload(client, model) is False
    assert seen == []


@pytest.mark.asyncio
async def test_ollama_unloads_with_keep_alive_zero_and_no_prompt():
    seen: list = []
    async with _ollama(seen) as client:
        assert await _unload(client, "qwen2.5:7b", backend_type="ollama",
                             base_url="http://127.0.0.1:11434") is True
    # keep_alive 0 with no prompt is Ollama's immediate-unload idiom: the
    # model is unloaded, never generated from.
    assert seen == [("POST", "/api/generate", {"model": "qwen2.5:7b", "keep_alive": 0})]


@pytest.mark.asyncio
async def test_ollama_unknown_model_is_a_failed_unload():
    seen: list = []
    async with _ollama(seen) as client:
        assert await _unload(client, "nope", backend_type="ollama",
                             base_url="http://127.0.0.1:11434") is False
    assert seen == [("POST", "/api/generate", {"model": "nope", "keep_alive": 0})]


@pytest.mark.asyncio
async def test_rkllama_unloads_via_explicit_route():
    seen: list = []
    async with _rkllama(seen) as client:
        assert await _unload(client, "qwen-rk", backend_type="rkllama",
                             base_url="http://127.0.0.1:7833") is True
    assert seen == [("POST", "/unload_model", {"model": "qwen-rk"})]


@pytest.mark.asyncio
async def test_rkllama_unknown_model_is_a_failed_unload():
    seen: list = []
    async with _rkllama(seen) as client:
        assert await _unload(client, "nope", backend_type="rkllama",
                             base_url="http://127.0.0.1:7833") is False
    assert seen == [("POST", "/unload_model", {"model": "nope"})]


@pytest.mark.asyncio
async def test_ollama_server_error_is_a_failed_unload():
    def boom(request):
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        assert await _unload(client, "qwen2.5:7b", backend_type="ollama",
                             base_url="http://127.0.0.1:11434") is False


@pytest.mark.asyncio
async def test_unsupported_type_logs_why_and_sends_nothing(caplog):
    # "Cannot unload" (no unloader for the type) must be distinguishable
    # from "unload failed": no request is sent, and the log says why.
    seen: list = []
    async with _ollama(seen) as client:
        with caplog.at_level(logging.WARNING, logger="tinyagentos.backend_unload"):
            assert await _unload(client, "qwen2.5:7b", backend_type="llama-cpp",
                                 base_url="http://127.0.0.1:11434") is False
    assert seen == []
    assert any("no unloader" in r.message and "llama-cpp" in r.message
               for r in caplog.records)


def test_unload_capable_registry():
    assert UNLOAD_CAPABLE_TYPES == frozenset({"llama-swap", "ollama", "rkllama"})
    assert unload_capable("llama-swap")
    assert unload_capable("ollama") and unload_capable("rkllama")
    assert not unload_capable("llama-cpp") and not unload_capable("vllm")
    # hailo-ollama is a deliberate follow-up, not silently included (plan B1).
    assert not unload_capable("hailo-ollama")


def test_no_http_route_reaches_unload():
    """Unload is the queue's eviction mechanism (spec 3.3), not an endpoint."""
    from pathlib import Path

    import tinyagentos

    routes = Path(tinyagentos.__file__).parent / "routes"
    assert [p.name for p in routes.rglob("*.py") if "backend_unload" in p.read_text()] == []
