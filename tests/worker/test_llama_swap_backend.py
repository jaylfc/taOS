"""llama-swap backend type (#3229): configured via TAOS_EXTRA_BACKENDS, models
from /v1/models, loaded_models from /running, and no double report with the
generic /v1/models types on the same endpoint."""
from __future__ import annotations

import pytest

from tinyagentos.worker import agent as agent_mod
from tinyagentos.worker.agent import (
    WorkerAgent,
    _DEFAULT_PROBE_CANDIDATES,
    _parse_extra_backends,
    _is_local_llama_swap_model,
    _parse_llama_swap_running,
)

# Captured 2026-10-02 from llama-swap v261 built from source (commit 42d8a5d),
# config: qwen-chat (alias qwen, ttl 300), org/embed-model, hidden-one
# (unlisted), after one chat request to "qwen". Only "cmd" is shortened.
RUNNING = {"running": [{
    "model": "qwen-chat", "state": "ready",
    "cmd": "simple-responder --port 5802 --model qwen-chat --silent",
    "proxy": "http://127.0.0.1:5802", "ttl": 300, "name": "Qwen chat", "description": "",
}]}
MODELS = {"data": [
    {"id": "org/embed-model", "object": "model", "created": 1790958372,
     "owned_by": "llama-swap", "meta": {"llamaswap": {"type": "model"}},
     "status": {"value": "unloaded"}},
    {"id": "qwen-chat", "object": "model", "created": 1790958372,
     "owned_by": "llama-swap", "name": "Qwen chat",
     "meta": {"llamaswap": {"aliases": ["qwen"], "type": "model"}},
     "status": {"value": "loaded"}},
], "object": "list"}
# A peer entry as v261 renders it (internal/server/api.go handleListModels):
# the model runs on another llama-swap, not on this worker.
PEER = {"id": "gpu-box/llama-70b", "object": "model", "created": 1790958372,
        "owned_by": "llama-swap", "name": "gpu-box: llama-70b",
        "meta": {"llamaswap": {"type": "peer", "peerID": "gpu-box"}},
        "status": {"value": "unloaded"}}


@pytest.fixture(autouse=True)
def _fresh_warning_cache():
    agent_mod._warned_probe_entries.clear()
    yield
    agent_mod._warned_probe_entries.clear()


class _Resp:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _fake_httpx(routes, requested=None):
    """httpx.AsyncClient stand-in: *routes* maps full URL -> (status, body);
    an origin with no route at all refuses the connection, a missing path on
    a live origin is a 404 (what llama.cpp answers for /running)."""
    origins = {"/".join(u.split("/")[:3]) for u in routes}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            if requested is not None:
                requested.append(url)
            if "/".join(url.split("/")[:3]) not in origins:
                raise ConnectionError("connection refused")
            status, body = routes.get(url, (404, ValueError("404 page not found")))
            return _Resp(status, body)

    return _Client


async def _detect(monkeypatch, routes, extra, requested=None):
    monkeypatch.setenv("TAOS_EXTRA_BACKENDS", extra)
    monkeypatch.setattr("tinyagentos.worker.worker_manifest.load_manifest",
                        lambda: {"resource_id": "", "models": []})
    monkeypatch.setattr("tinyagentos.worker.agent.httpx.AsyncClient", _fake_httpx(routes, requested))
    return await WorkerAgent("http://localhost:6969").detect_backends()


def _swap(base):
    return {f"{base}/running": (200, RUNNING), f"{base}/v1/models": (200, MODELS)}


# --- type registration ------------------------------------------------------


def test_llama_swap_is_a_local_probeable_type():
    from tinyagentos.backend_adapters import get_adapter
    from tinyagentos.llm_usage import LOCAL_BACKENDS
    from tinyagentos.providers import BACKEND_TYPE_MAP, LOCAL_TYPES
    from tinyagentos.scheduler.backend_catalog import BACKEND_CAPABILITIES

    assert _parse_extra_backends("llama-swap=http://127.0.0.1:8080") == [
        ("llama-swap", "http://127.0.0.1:8080"),
    ]
    assert "llama-swap" in LOCAL_TYPES and "llama-swap" in LOCAL_BACKENDS
    assert BACKEND_TYPE_MAP["llama-swap"] == "openai"
    assert BACKEND_CAPABILITIES["llama-swap"] == {"llm-chat", "embedding", "reranking"}
    get_adapter("llama-swap")  # raises for an unknown type


def test_no_default_llama_swap_candidate():
    # Its default port 8080 is shared with llama.cpp and legacy rkllama: opt-in only.
    assert all(t != "llama-swap" for t, _ in _DEFAULT_PROBE_CANDIDATES)


# --- /running parsing --------------------------------------------------------


def test_running_parses_captured_reply():
    assert _parse_llama_swap_running(RUNNING) == [{"name": "qwen-chat", "size_mb": 0}]
    assert _parse_llama_swap_running({"running": []}) == []


def test_running_keeps_starting_drops_stopping_and_bad_entries():
    body = {"running": [
        {"model": "a", "state": "starting"},
        {"model": "b", "state": "stopping"},
        {"model": "c"},                       # no state: still listed as running
        {"model": "d", "state": ["ready"]},   # junk state does not crash
        {"state": "ready"}, {"model": ""}, {"model": 7}, "e", None,
    ]}
    assert [m["name"] for m in _parse_llama_swap_running(body)] == ["a", "c", "d"]


@pytest.mark.guards("tinyagentos.worker.agent:_parse_llama_swap_running")
@pytest.mark.parametrize("body", [
    None, [], "running", {}, {"running": None}, {"running": {"model": "a"}},
    {"data": [{"id": "m"}]},  # a /v1/models-shaped reply is not /running
])
def test_running_rejects_non_llama_swap_replies(body):
    assert _parse_llama_swap_running(body) is None


# --- end to end through detect_backends -------------------------------------


@pytest.mark.asyncio
class TestDetect:
    async def test_configured_llama_swap_reports_models_and_loaded(self, monkeypatch):
        backends = await _detect(monkeypatch, _swap("http://127.0.0.1:9292"),
                                 "llama-swap=http://127.0.0.1:9292")
        assert len(backends) == 1
        b = backends[0]
        assert (b["name"], b["type"], b["url"]) == (
            "llama-swap:9292", "llama-swap", "http://127.0.0.1:9292")
        assert [m["name"] for m in b["models"]] == ["org/embed-model", "qwen-chat"]
        assert b["loaded_models"] == [{"name": "qwen-chat", "size_mb": 0}]
        assert b["capabilities"] == ["embedding", "llm-chat", "reranking"]

    @pytest.mark.guards(
        "tinyagentos.worker.agent:WorkerAgent.detect_backends",
        replace=[('or _candidate_key(b["type"], b["url"])[1:] not in swap_endpoints]',
                  "or True]")],
    )
    async def test_llama_swap_on_a_default_port_is_not_double_reported(self, monkeypatch):
        # llama-cpp and vllm default to :8000 and would both claim it via /v1/models.
        routes = {**_swap("http://localhost:8000"), **_swap("http://127.0.0.1:8000")}
        backends = await _detect(monkeypatch, routes, "llama-swap=http://127.0.0.1:8000")
        assert [b["name"] for b in backends] == ["llama-swap:8000"]

    @pytest.mark.guards(
        "tinyagentos.worker.agent:_probe_llama_swap",
        replace=[("if loaded is None:", "if False:")],
    )
    async def test_plain_llama_cpp_is_not_reported_as_llama_swap(self, monkeypatch):
        routes = {f"{base}/v1/models": (200, {"data": [{"id": "m"}]})
                  for base in ("http://localhost:8000", "http://127.0.0.1:8000")}
        backends = await _detect(monkeypatch, routes, "llama-swap=http://127.0.0.1:8000")
        assert [b["name"] for b in backends] == ["llama-cpp:8000", "vllm:8000"]

    @pytest.mark.parametrize("models_reply", [
        (500, {}), (200, ValueError("not json")), (200, [{"id": "m"}]), (200, {"data": None}),
    ])
    async def test_malformed_models_reply_is_not_detected(self, monkeypatch, models_reply):
        routes = {"http://127.0.0.1:9292/running": (200, RUNNING),
                  "http://127.0.0.1:9292/v1/models": models_reply}
        assert await _detect(monkeypatch, routes, "llama-swap=http://127.0.0.1:9292") == []

    async def test_one_running_and_one_models_request_per_heartbeat(self, monkeypatch):
        requested: list = []
        await _detect(monkeypatch, _swap("http://127.0.0.1:9292"),
                      "llama-swap=http://127.0.0.1:9292", requested)
        assert sorted(u for u in requested if ":9292" in u) == [
            "http://127.0.0.1:9292/running", "http://127.0.0.1:9292/v1/models"]

    @pytest.mark.guards("tinyagentos.worker.agent:_is_local_llama_swap_model",
                        replace=[('return kind is None or kind == "model"', "return True")])
    async def test_peer_models_are_not_this_workers(self, monkeypatch):
        routes = {"http://127.0.0.1:9292/running": (200, RUNNING),
                  "http://127.0.0.1:9292/v1/models": (
                      200, {**MODELS, "data": MODELS["data"] + [PEER]})}
        backends = await _detect(monkeypatch, routes, "llama-swap=http://127.0.0.1:9292")
        assert [m["name"] for m in backends[0]["models"]] == ["org/embed-model", "qwen-chat"]

    async def test_bad_model_entries_skipped(self, monkeypatch):
        routes = {"http://127.0.0.1:9292/running": (200, {"running": []}),
                  "http://127.0.0.1:9292/v1/models": (
                      200, {"data": [{"id": "ok"}, {"id": ""}, {"name": "x"}, "y", None]})}
        backends = await _detect(monkeypatch, routes, "llama-swap=http://127.0.0.1:9292")
        assert [m["name"] for m in backends[0]["models"]] == ["ok"]
        assert backends[0]["loaded_models"] == []


@pytest.mark.parametrize("entry, local", [
    ({"id": "m", "meta": {"llamaswap": {"type": "model"}}}, True),
    ({"id": "m"}, True),                                   # older builds: no meta
    ({"id": "m", "meta": {"llamaswap": {"type": "selector"}}}, False),
    ({"id": "m", "meta": {"llamaswap": {"type": "alias"}}}, False),
    (PEER, False),
])
def test_local_model_filter(entry, local):
    assert _is_local_llama_swap_model(entry) is local
