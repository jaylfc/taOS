"""Tests for the shared model-layout path helpers."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyagentos.installers.model_paths import (
    backend_model_dir,
    family_from_manifest,
    filename_from_url,
    models_root,
)


class TestFamilyFromManifest:
    def test_string_id_takes_first_dash_token(self):
        assert family_from_manifest("gemma-4-e2b-gguf") == "gemma"
        assert family_from_manifest("llama-3.2-1b") == "llama"
        assert family_from_manifest("bge-m3") == "bge"

    def test_no_dash_keeps_whole_id(self):
        # qwen3.5-9b -> "qwen3.5"; qwen35 with no dash -> "qwen35"
        assert family_from_manifest("qwen3.5-9b") == "qwen3.5"
        assert family_from_manifest("singletoken") == "singletoken"

    def test_lowercases_token(self):
        assert family_from_manifest("Gemma-4-E2B-GGUF") == "gemma"

    def test_explicit_family_wins(self):
        class Manifest:
            id = "paligemma-2"
            family = "gemma"

        assert family_from_manifest(Manifest()) == "gemma"

    def test_explicit_family_from_dict(self):
        assert family_from_manifest({"id": "anything", "family": "Custom"}) == "custom"

    def test_dict_without_family_falls_back_to_id(self):
        assert family_from_manifest({"id": "qwen3.5-9b"}) == "qwen3.5"

    def test_object_with_id_attr(self):
        class Manifest:
            id = "gemma-4-e2b-gguf"

        assert family_from_manifest(Manifest()) == "gemma"

    def test_empty_id_falls_back_to_uncategorised(self):
        assert family_from_manifest("") == "uncategorised"


class TestBackendModelDir:
    def test_builds_full_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path))
        result = backend_model_dir("rk-llama.cpp", "gemma-4-e2b-gguf")
        assert result == tmp_path / "rk-llama.cpp" / "gemma" / "gemma-4-e2b-gguf"

    def test_uses_explicit_family(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path))
        result = backend_model_dir("ollama", {"id": "paligemma-2", "family": "gemma"})
        assert result == tmp_path / "ollama" / "gemma" / "paligemma-2"

    def test_pure_path_builder_no_mkdir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path))
        result = backend_model_dir("download", "test-model")
        assert not result.exists()  # builder doesn't touch the filesystem

    def test_raises_on_empty_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path))
        with pytest.raises(ValueError):
            backend_model_dir("rk-llama.cpp", "")


class TestModelsRoot:
    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "alt"))
        assert models_root() == tmp_path / "alt"

    def test_default_is_home_models(self, monkeypatch):
        monkeypatch.delenv("TAOS_MODELS_ROOT", raising=False)
        # Just assert the relationship to home; absolute path varies per CI
        assert models_root() == Path.home() / "models"


class TestFilenameFromUrl:
    def test_extracts_basename(self):
        url = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
        assert filename_from_url(url, "fallback.bin") == "gemma-4-E2B-it-Q4_K_M.gguf"

    def test_falls_back_for_no_extension(self):
        # An opaque CDN URL with no clean extension hits the fallback.
        assert filename_from_url("https://example.com/redirect/abc123", "model.gguf") == "model.gguf"

    def test_empty_url_uses_fallback(self):
        assert filename_from_url("", "fallback.gguf") == "fallback.gguf"

    def test_sanitises_unsafe_chars(self):
        url = "https://example.com/path/weird name!.gguf"
        out = filename_from_url(url, "fallback.bin")
        # Spaces and ! get replaced with underscores
        assert " " not in out
        assert "!" not in out
        assert out.endswith(".gguf")
