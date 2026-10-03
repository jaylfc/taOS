"""Residency backend unload adapters (#3230 slice B1 interface; llama-swap #3229).

Replies mirror llama-swap v261 as observed 2026-10-02 against a source build
(commit 42d8a5d): "OK" 200 for a known model whether or not it was running
(aliases resolve), a JSON 404 for an unknown one.
"""
from __future__ import annotations

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


def test_unload_capable_registry():
    assert UNLOAD_CAPABLE_TYPES == frozenset({"llama-swap"})
    assert unload_capable("llama-swap")
    assert not unload_capable("llama-cpp") and not unload_capable("vllm")


def test_no_http_route_reaches_unload():
    """Unload is the queue's eviction mechanism (spec 3.3), not an endpoint."""
    from pathlib import Path

    import tinyagentos

    routes = Path(tinyagentos.__file__).parent / "routes"
    assert [p.name for p in routes.rglob("*.py") if "backend_unload" in p.read_text()] == []
