"""Tests for the local-installed-model registration path in
generate_litellm_config — the fix that lets the agent picker actually
chat with locally-installed models like gemma-4-e2b-gguf."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tinyagentos.llm_proxy import generate_litellm_config


def _fake_registry(manifests: list[dict], installed_ids: list[str]):
    """Build a stub object exposing the AppRegistry surface that
    generate_litellm_config uses (list_installed + get).
    """
    by_id = {m["id"]: SimpleNamespace(**m) for m in manifests}

    def _get(app_id):
        return by_id.get(app_id)

    return SimpleNamespace(
        list_installed=lambda: [{"id": i} for i in installed_ids],
        get=_get,
    )


def _gemma_manifest(app_id: str, backend_id: str) -> dict:
    return {
        "id": app_id,
        "name": app_id,
        "type": "model",
        "variants": [
            {
                "id": "q4_k_m",
                "format": "gguf",
                "download_url": f"https://example.com/{app_id}.gguf",
                "requires": {"backends": [{"id": backend_id}]},
            }
        ],
        "context_window": 32768,
    }


def _local_backend(name="local-rk-llama-cpp", url="http://localhost:8090"):
    return {
        "name": name,
        "type": "openai-compatible",
        "url": url,
        "priority": 99,
        "enabled": True,
    }


class TestLocalInstalledModelRegistration:
    def test_installed_model_registered_under_manifest_id(self):
        """The whole point: chatting with the agent picker's selected
        model name must reach a configured LiteLLM model_name."""
        registry = _fake_registry(
            manifests=[_gemma_manifest("gemma-4-e2b-gguf", "rk-llama-cpp")],
            installed_ids=["gemma-4-e2b-gguf"],
        )

        config = generate_litellm_config([_local_backend()], registry=registry)
        names = [e["model_name"] for e in config["model_list"]]
        assert "gemma-4-e2b-gguf" in names, names

        entry = next(e for e in config["model_list"] if e["model_name"] == "gemma-4-e2b-gguf")
        assert entry["litellm_params"]["api_base"] == "http://localhost:8090"
        assert entry["litellm_params"]["model"] == "openai/gemma-4-e2b-gguf"
        assert entry["metadata"]["source"] == "local-installed"

    def test_only_compatible_models_register(self):
        """A model whose variants don't list this backend's id must not
        be registered against this backend — wrong route would 400."""
        registry = _fake_registry(
            manifests=[
                _gemma_manifest("gemma-4-e2b-gguf", "rk-llama-cpp"),
                # This one targets a different backend (ollama) — must be skipped
                _gemma_manifest("ollama-only-model", "ollama"),
            ],
            installed_ids=["gemma-4-e2b-gguf", "ollama-only-model"],
        )

        config = generate_litellm_config([_local_backend()], registry=registry)
        names = [e["model_name"] for e in config["model_list"]]
        assert "gemma-4-e2b-gguf" in names
        assert "ollama-only-model" not in names

    def test_no_registry_no_local_entries(self):
        """Existing behaviour without the registry must be preserved —
        no installed-model entries when the caller doesn't pass it."""
        config = generate_litellm_config([_local_backend()])
        names = [e["model_name"] for e in config["model_list"]]
        # "default" alias should still be there for the openai-compatible backend
        assert "default" in names
        # No per-installed-model entries because no registry was supplied
        assert all(not n.startswith("gemma") for n in names)

    def test_non_local_named_backend_skipped(self):
        """Cloud backends (name doesn't start with 'local-') must be
        unaffected — the per-installed-model branch only runs for
        backends auto-registered from local service manifests."""
        cloud = {
            "name": "openai-prod",  # not 'local-...'
            "type": "openai",
            "url": "https://api.openai.com/v1",
            "priority": 1,
            "models": [{"id": "gpt-4o"}],
        }
        registry = _fake_registry(
            manifests=[_gemma_manifest("gemma-4-e2b-gguf", "rk-llama-cpp")],
            installed_ids=["gemma-4-e2b-gguf"],
        )
        config = generate_litellm_config([cloud], registry=registry)
        names = [e["model_name"] for e in config["model_list"]]
        # Cloud per-model entries still present
        assert "gpt-4o" in names
        # Local manifest never registers under the cloud backend
        assert "gemma-4-e2b-gguf" not in names

    def test_no_duplicate_when_cloud_loop_already_added_name(self):
        """If a cloud-style backend.models[] entry already registered the
        manifest id, the local-installed branch must not double-register."""
        backend = {
            "name": "local-rk-llama-cpp",
            "type": "openai-compatible",
            "url": "http://localhost:8090",
            "priority": 1,
            # Pre-declared models[] (unlikely for local, but the dedupe
            # logic must hold even if a future caller adds one)
            "models": [{"id": "gemma-4-e2b-gguf"}],
        }
        registry = _fake_registry(
            manifests=[_gemma_manifest("gemma-4-e2b-gguf", "rk-llama-cpp")],
            installed_ids=["gemma-4-e2b-gguf"],
        )
        config = generate_litellm_config([backend], registry=registry)
        gemma_entries = [e for e in config["model_list"] if e["model_name"] == "gemma-4-e2b-gguf"]
        assert len(gemma_entries) == 1
