"""Regression tests for openai-compatible provider routing in generate_litellm_config.

Covers the bug where openai-compatible backends did not get api_base set,
causing LiteLLM to silently fall back to https://api.openai.com instead of
routing to the user's custom endpoint.
"""
import pytest
from tinyagentos.llm_proxy import generate_litellm_config, CLOUD_BACKEND_TYPES

CUSTOM_URL = "http://192.0.2.1:8000/v1"
OPENAI_DEFAULT = "https://api.openai.com"


def _build_backend(models=None):
    return {
        "name": "my-custom-endpoint",
        "type": "openai-compatible",
        "url": CUSTOM_URL,
        "priority": 1,
        "models": models or [{"id": "my-model-7b"}, {"id": "my-model-13b"}],
    }


def test_openai_compatible_in_cloud_backend_types():
    """openai-compatible must be recognised as a cloud type."""
    assert "openai-compatible" in CLOUD_BACKEND_TYPES


def test_default_entry_has_custom_api_base():
    """The default model_list entry must carry the user's URL, not the OpenAI default."""
    config = generate_litellm_config([_build_backend()])
    default_entries = [
        e for e in config["model_list"] if e["model_name"] == "default"
    ]
    assert default_entries, "No default entry generated"
    params = default_entries[0]["litellm_params"]
    assert "api_base" in params, "api_base missing from default entry"
    assert params["api_base"] == CUSTOM_URL
    assert params["api_base"] != OPENAI_DEFAULT


def test_per_model_entries_have_custom_api_base():
    """Each declared model entry must carry the user's URL as api_base."""
    config = generate_litellm_config([_build_backend()])
    per_model = [
        e for e in config["model_list"]
        if e["model_name"] in ("my-model-7b", "my-model-13b")
    ]
    assert len(per_model) == 2, f"Expected 2 per-model entries, got {len(per_model)}"
    for entry in per_model:
        params = entry["litellm_params"]
        assert "api_base" in params, f"api_base missing from {entry['model_name']} entry"
        assert params["api_base"] == CUSTOM_URL
        assert params["api_base"] != OPENAI_DEFAULT


def test_per_model_uses_openai_prefix():
    """openai-compatible backends should use the openai/ LiteLLM prefix."""
    config = generate_litellm_config([_build_backend()])
    per_model = [
        e for e in config["model_list"]
        if e["model_name"] == "my-model-7b"
    ]
    assert per_model
    model_field = per_model[0]["litellm_params"]["model"]
    assert model_field == "openai/my-model-7b", f"Unexpected prefix: {model_field}"


def test_no_openai_default_leak_when_url_set():
    """Confirm the OpenAI default URL does not appear anywhere in the config
    when the user has provided a custom URL."""
    import yaml
    config = generate_litellm_config([_build_backend()])
    serialised = yaml.dump(config)
    assert OPENAI_DEFAULT not in serialised, (
        "OpenAI default URL leaked into config — requests would go to the wrong endpoint"
    )
