"""Unit tests for tinyagentos/llm_gateway/embeddings.py discovery on the gateway
request path.

Covers defect A: the gateway request path must probe llama-cpp and lmstudio
(OpenAI-compatible) backends the same way it probes ollama-family backends, so
embedding routes exist on the gateway path.

Covers auth: the probe must send Authorization header when the backend has an
api_key or api_key_secret so key-protected llama-cpp/lmstudio servers return
models instead of 401.
"""
from __future__ import annotations

import types

import httpx
import pytest

import tinyagentos.litellm_config as cfg_mod
from tinyagentos.llm_gateway.embeddings import embedding_table

URL = "http://llama-cpp.local:8080"


def _state(backends):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(backends=list(backends)),
        registry=None,
    )


@pytest.mark.asyncio
async def test_embedding_table_discovers_llama_cpp_backend_models(monkeypatch):
    """embedding_table on a state whose config.backends holds one llama-cpp
    backend, with litellm_config._discover_openai_compatible_models monkeypatched
    to return ["nomic-embed-text-v1.5"], returns an entry with
    model_info.mode == "embedding" for that model."""
    state = _state([
        {"name": "llama-cpp-local", "type": "llama-cpp", "url": URL,
         "api_key_secret": "LLAMA_KEY"},
    ])
    monkeypatch.setattr(
        cfg_mod, "_discover_openai_compatible_models",
        lambda url, timeout=2.0, api_key=None: ["nomic-embed-text-v1.5"],
    )
    table = await embedding_table(state)
    embed_entries = [e for e in table if e.get("model_info", {}).get("mode") == "embedding"]
    assert any(e["model_name"] == "nomic-embed-text-v1.5" for e in embed_entries)


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {"data": [{"id": "nomic-embed-text-v1.5"}]}

    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_discovery_probe_sends_authorization_header_when_api_key_provided(monkeypatch):
    """The /models probe must include Authorization: Bearer <key> when the
    backend has an api_key, so a key-protected llama-cpp/lmstudio server
    returns models instead of 401."""
    recorded = {}

    def fake_get(url, timeout=2.0, headers=None):
        recorded["url"] = url
        recorded["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(cfg_mod.httpx, "get", fake_get)

    state = _state([
        {"name": "llama-cpp-local", "type": "llama-cpp", "url": URL, "api_key": "sk-test"},
    ])
    table = await embedding_table(state)

    embed_entries = [e for e in table if e.get("model_info", {}).get("mode") == "embedding"]
    assert any(e["model_name"] == "nomic-embed-text-v1.5" for e in embed_entries)
    assert recorded["headers"] is not None
    assert recorded["headers"].get("Authorization") == "Bearer sk-test"


@pytest.mark.asyncio
async def test_discovery_probe_sends_no_authorization_header_when_no_api_key(monkeypatch):
    """The /models probe must not include an Authorization header when the
    backend has no api_key or api_key_secret, so keyless local servers work."""
    recorded = {}

    def fake_get(url, timeout=2.0, headers=None):
        recorded["url"] = url
        recorded["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(cfg_mod.httpx, "get", fake_get)

    state = _state([
        {"name": "llama-cpp-local", "type": "llama-cpp", "url": URL},
    ])
    table = await embedding_table(state)

    embed_entries = [e for e in table if e.get("model_info", {}).get("mode") == "embedding"]
    assert any(e["model_name"] == "nomic-embed-text-v1.5" for e in embed_entries)
    assert recorded["headers"] is None or recorded["headers"].get("Authorization") is None
