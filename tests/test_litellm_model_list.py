"""The gateway routing table has ONE builder: ``build_model_list``.

The LiteLLM proxy config that also wrapped it is gone (LiteLLM removal stage
2b-2a); what is left is the builder itself and its discovery switch.
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
