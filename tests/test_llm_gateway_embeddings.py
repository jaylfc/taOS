"""LiteLLM removal stage 2a (tsk-ilqzq6): the gateway serves embeddings and
mirrors ``reasoning`` as ``reasoning_content``.

Every test drives the agent listener (what an agent's ``127.0.0.1:4000``
reaches once cut over) around the REAL controller app, with an agent key
from the local key store. LiteLLM is ABSENT: its port answers connection
refused, so anything that still relays to it shows up as a 502, not a pass.
Only the backends are faked (respx).
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.litellm_config import EMBEDDING_ALIAS
from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

LITELLM_PORT = 4000
LLAMA = "http://llm.test:8080/v1"
NPU = "http://npu.test:8080"
UPSTREAM_KEY = "sk-upstream-DO-NOT-LEAK-5b1e"

BACKENDS = [
    # llama.cpp-style OpenAI-compatible server: chat + an embedding GGUF.
    {"name": "local-llama", "type": "openai-compatible", "url": LLAMA,
     "models": [{"id": "qwen3-8b"}, {"id": "nomic-embed-gguf"}],
     "api_key": UPSTREAM_KEY, "priority": 1},
    # Ollama-shaped NPU box: its embedding model is discovered from /api/tags
    # and claims the taos-embedding-default alias, exactly as for LiteLLM.
    {"name": "npu", "type": "rkllama", "url": NPU, "priority": 2},
]


def _embedding_vector() -> list[float]:
    return [0.1, 0.2, 0.3]


@pytest.fixture
def gw(tmp_data_dir, monkeypatch):
    from tinyagentos.app import create_app
    from tinyagentos.llm_gateway.listener import create_agent_listener_app

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    cfg_path = tmp_data_dir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["backends"] = BACKENDS
    cfg_path.write_text(yaml.dump(cfg))
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    listener = create_agent_listener_app(app)
    store = LiteLLMKeyStore(default_keystore_path(tmp_data_dir))
    return app, listener, store


def _client(listener) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=listener), base_url="http://127.0.0.1:4000")


def _mock_backends(router: respx.Router):
    """The two local backends up, LiteLLM down."""
    router.get(f"{NPU}/api/tags").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "nomic-embed-text"}, {"name": "qwen2.5:3b"}]}))
    ollama_embed = router.post(f"{NPU}/api/embed").mock(return_value=httpx.Response(
        200, json={"model": "nomic-embed-text", "embeddings": [_embedding_vector()],
                   "prompt_eval_count": 3}))
    llama_embed = router.post(f"{LLAMA}/embeddings").mock(return_value=httpx.Response(
        200, json={"object": "list", "model": "nomic-embed-gguf",
                   "data": [{"object": "embedding", "index": 0, "embedding": _embedding_vector()}],
                   "usage": {"prompt_tokens": 4, "total_tokens": 4}}))
    litellm = router.route(host="127.0.0.1", port=LITELLM_PORT).mock(
        side_effect=httpx.ConnectError("connection refused"))
    return ollama_embed, llama_embed, litellm


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listener_embeds_via_the_alias_on_an_ollama_backend_without_litellm(gw):
    """taos-embedding-default (TAOS_EMBEDDING_MODEL) reaches the Ollama-shaped
    backend's /api/embed, the call LiteLLM made, and comes back OpenAI-shaped."""
    _app, listener, store = gw
    key = store.mint("emb-agent", [EMBEDDING_ALIAS])
    with respx.mock(assert_all_called=False) as router:
        ollama_embed, llama_embed, litellm = _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/v1/embeddings", json={"model": EMBEDDING_ALIAS, "input": "hello"},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"] == [{"object": "embedding", "index": 0, "embedding": _embedding_vector()}]
    assert body["usage"]["prompt_tokens"] == 3
    assert ollama_embed.call_count == 1
    assert json.loads(ollama_embed.calls[0].request.content) == {
        "model": "nomic-embed-text", "input": ["hello"]}
    assert not litellm.called and not llama_embed.called


@pytest.mark.asyncio
async def test_listener_embeds_a_concrete_model_on_an_openai_compatible_backend(gw):
    """A model on a llama.cpp (OpenAI-compatible) backend: its own /embeddings,
    the model rewritten to the backend's id, the backend's key sent, the
    caller's key never forwarded."""
    _app, listener, store = gw
    key = store.mint("emb-agent", ["nomic-embed-gguf"])
    with respx.mock(assert_all_called=False) as router:
        _ollama, llama_embed, litellm = _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/embeddings", json={"model": "nomic-embed-gguf", "input": ["a", "b"],
                                                      "encoding_format": "float"},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"][0]["embedding"] == _embedding_vector()
    sent = llama_embed.calls[0].request
    assert json.loads(sent.content) == {"model": "nomic-embed-gguf", "input": ["a", "b"],
                                        "encoding_format": "float"}
    assert sent.headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert not litellm.called


@pytest.mark.asyncio
async def test_embeddings_model_not_in_the_agents_allowlist_is_403(gw):
    _app, listener, store = gw
    key = store.mint("chat-only", ["qwen3-8b"])
    with respx.mock(assert_all_called=False) as router:
        ollama_embed, llama_embed, litellm = _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/v1/embeddings", json={"model": EMBEDDING_ALIAS, "input": "hello"},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "model_not_permitted"
    assert not (ollama_embed.called or llama_embed.called or litellm.called)


@pytest.mark.asyncio
async def test_default_deployed_agent_can_embed_through_the_listener(gw):
    """A key minted with a chat model plus taos-embedding-default (the shape the
    deployer now produces) can call /v1/embeddings and gets 200, not 403."""
    _app, listener, store = gw
    key = store.mint("deployed-emb", ["qwen3-8b", EMBEDDING_ALIAS])
    with respx.mock(assert_all_called=False) as router:
        ollama_embed, llama_embed, litellm = _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/v1/embeddings", json={"model": EMBEDDING_ALIAS, "input": "hello"},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_embeddings_without_a_key_is_401_and_reaches_nothing(gw):
    _app, listener, _store = gw
    with respx.mock(assert_all_called=False) as router:
        ollama_embed, llama_embed, litellm = _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/v1/embeddings", json={"model": EMBEDDING_ALIAS, "input": "hello"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "invalid_api_key"
    assert not (ollama_embed.called or llama_embed.called or litellm.called)


@pytest.mark.asyncio
async def test_embeddings_usage_is_recorded_for_the_agent(gw, monkeypatch):
    _app, listener, store = gw
    key = store.mint("emb-agent", [EMBEDDING_ALIAS])
    recorded = []

    async def fake_record_trace(state, principal, model, usage, cost, backend, *rest, **kw):
        recorded.append({"principal": principal, "model": model, "in": usage.input_tokens,
                         "out": usage.output_tokens, "backend": backend})

    monkeypatch.setattr("tinyagentos.llm_gateway.forward._record_trace", fake_record_trace)
    with respx.mock(assert_all_called=False) as router:
        _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/v1/embeddings", json={"model": EMBEDDING_ALIAS, "input": "hello"},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    assert recorded == [{"principal": "emb-agent", "model": "nomic-embed-text", "in": 3,
                         "out": 0, "backend": "npu"}]


@pytest.mark.asyncio
async def test_embeddings_notify_lifecycle_keepalive(gw):
    """LiteLLM's callback reset the backend's keep-alive after embeddings too;
    the gateway does it in-process, once per successful call."""
    app, listener, store = gw
    calls = []
    app.state.lifecycle_manager = type("L", (), {"notify_task_complete": lambda self, n: calls.append(n)})()
    key = store.mint("emb-agent", [EMBEDDING_ALIAS])
    with respx.mock(assert_all_called=False) as router:
        _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/v1/embeddings", json={"model": EMBEDDING_ALIAS, "input": "hello"},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    assert calls == ["npu"]


@pytest.mark.asyncio
async def test_embeddings_unknown_model_is_404(gw):
    _app, listener, store = gw
    key = store.mint("emb-agent", ["no-such-embedder"])
    with respx.mock(assert_all_called=False) as router:
        _mock_backends(router)
        async with _client(listener) as c:
            resp = await c.post("/v1/embeddings", json={"model": "no-such-embedder", "input": "x"},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "model_not_found"


# ---------------------------------------------------------------------------
# reasoning -> reasoning_content (what LiteLLM emitted; hermes/openclaw read either)
# ---------------------------------------------------------------------------


def _completion(message: dict) -> dict:
    return {"id": "c1", "object": "chat.completion", "created": 1, "model": "qwen3-8b",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}


@pytest.mark.asyncio
async def test_non_stream_response_carries_reasoning_content_mirroring_reasoning(gw):
    _app, listener, store = gw
    key = store.mint("thinker", ["qwen3-8b"])
    details = [{"type": "reasoning.text", "text": "let me think"}]
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LLAMA}/chat/completions").mock(return_value=httpx.Response(200, json=_completion(
            {"role": "assistant", "content": "pong", "reasoning": "let me think",
             "reasoning_details": details})))
        async with _client(listener) as c:
            resp = await c.post("/v1/chat/completions",
                                json={"model": "qwen3-8b", "messages": [{"role": "user", "content": "ping"}]},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    msg = resp.json()["choices"][0]["message"]
    assert msg["reasoning_content"] == "let me think"
    # upstream's own fields are kept as they were
    assert msg["reasoning"] == "let me think"
    assert msg["reasoning_details"] == details
    assert msg["content"] == "pong"


@pytest.mark.asyncio
async def test_stream_deltas_carry_reasoning_content_mirroring_reasoning(gw):
    _app, listener, store = gw
    key = store.mint("thinker", ["qwen3-8b"])

    def chunk(delta):
        return "data: " + json.dumps({"id": "c1", "object": "chat.completion.chunk",
                                      "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n"

    sse = (chunk({"role": "assistant", "reasoning": "hmm"})
           + chunk({"reasoning": " ok", "reasoning_details": [{"type": "reasoning.text", "text": " ok"}]})
           + chunk({"content": "pong"})
           + "data: [DONE]\n\n")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LLAMA}/chat/completions").mock(return_value=httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}))
        async with _client(listener) as c:
            resp = await c.post("/v1/chat/completions",
                                json={"model": "qwen3-8b", "stream": True,
                                      "messages": [{"role": "user", "content": "ping"}]},
                                headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    deltas = [json.loads(line[5:])["choices"][0]["delta"]
              for line in resp.text.split("\n\n") if line.startswith("data: {")]
    assert [d.get("reasoning_content") for d in deltas] == ["hmm", " ok", None]
    assert [d.get("reasoning") for d in deltas] == ["hmm", " ok", None]
    assert deltas[1]["reasoning_details"] == [{"type": "reasoning.text", "text": " ok"}]
    assert "reasoning_content" not in deltas[2]
    assert resp.text.endswith("data: [DONE]\n\n")


@pytest.mark.asyncio
async def test_upstream_reasoning_content_is_never_overwritten(gw):
    """A provider that already sends reasoning_content keeps its own value."""
    _app, listener, store = gw
    key = store.mint("thinker", ["qwen3-8b"])
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{LLAMA}/chat/completions").mock(return_value=httpx.Response(200, json=_completion(
            {"role": "assistant", "content": "x", "reasoning_content": "native"})))
        async with _client(listener) as c:
            resp = await c.post("/v1/chat/completions",
                                json={"model": "qwen3-8b", "messages": [{"role": "user", "content": "ping"}]},
                                headers={"Authorization": f"Bearer {key}"})
    msg = resp.json()["choices"][0]["message"]
    assert msg["reasoning_content"] == "native"
    assert "reasoning" not in msg
