"""The LiteLLM routing table has ONE builder.

`generate_litellm_config` (the LiteLLM proxy's config) and the in-process LLM
gateway (tinyagentos/llm_gateway) must route a model name to the same backend.
They can only be guaranteed to agree if both read the same table, so the pure
"model list" half of the config generator lives in `build_model_list` and the
config generator is a thin wrapper around it.
"""
from __future__ import annotations

import tinyagentos.litellm_config as lc

BACKENDS = [
    {
        "name": "local-llama",
        "type": "openai-compatible",
        "url": "http://llm.test:8080/v1/",
        "models": [{"id": "qwen3-8b"}, "gpt-small"],
        "api_key": "sk-upstream",
        "priority": 2,
    },
    {
        "name": "claude",
        "type": "anthropic",
        "url": "https://api.anthropic.com",
        "models": [{"id": "claude-x"}],
        "api_key_secret": "ANTHROPIC_KEY",
        "priority": 1,
    },
    {"name": "npu", "type": "rkllama", "url": "http://npu.test:8080", "priority": 3},
]


def test_config_model_list_is_exactly_build_model_list():
    discovered = {"http://npu.test:8080": ["nomic-embed-text", "qwen2.5"]}
    config = lc.generate_litellm_config(BACKENDS, master_key="k", discovered=discovered)
    assert config["model_list"] == lc.build_model_list(BACKENDS, discovered=discovered)


def test_generate_litellm_config_calls_build_model_list(monkeypatch):
    sentinel = [{"model_name": "sentinel", "litellm_params": {"model": "openai/sentinel"}}]
    seen = {}

    def fake(backends, default_model="default", **kw):
        seen["args"] = (backends, default_model, kw)
        return sentinel

    monkeypatch.setattr(lc, "build_model_list", fake)
    config = lc.generate_litellm_config(BACKENDS, master_key="k", discovered={})
    assert config["model_list"] is sentinel
    assert seen["args"][0] is BACKENDS


def test_discover_false_never_probes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("discover=False must not probe a backend")

    monkeypatch.setattr(lc, "_discover_ollama_models", boom)
    table = lc.build_model_list(BACKENDS, discover=False)
    names = [e["model_name"] for e in table]
    assert "qwen3-8b" in names and "claude-x" in names
    # Only the probe-derived embedding entries are absent.
    assert "nomic-embed-text" not in names


def test_table_is_priority_ordered_and_carries_backend_name():
    table = lc.build_model_list(BACKENDS, discover=False)
    assert table[0]["metadata"]["backend_name"] == "claude"
    q = next(e for e in table if e["model_name"] == "qwen3-8b")
    assert q["litellm_params"] == {
        "model": "openai/qwen3-8b",
        "api_base": "http://llm.test:8080/v1",
        "api_key": "sk-upstream",
    }
    assert q["metadata"]["backend_name"] == "local-llama"
