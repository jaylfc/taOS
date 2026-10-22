"""Unit tests for tinyagentos/llm_gateway/embeddings.py discovery on the gateway
request path.

Covers defect A: the gateway request path must probe llama-cpp and lmstudio
(OpenAI-compatible) backends the same way it probes ollama-family backends, so
embedding routes exist on the gateway path.
"""
from __future__ import annotations

import types

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
        lambda url, timeout=2.0: ["nomic-embed-text-v1.5"],
    )
    table = await embedding_table(state)
    embed_entries = [e for e in table if e.get("model_info", {}).get("mode") == "embedding"]
    assert any(e["model_name"] == "nomic-embed-text-v1.5" for e in embed_entries)
