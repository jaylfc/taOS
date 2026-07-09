"""Tests for tinyagentos.worker.worker_manifest — local manifest parser."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tinyagentos.worker.worker_manifest import (
    DEFAULT_MANIFEST_PATH,
    SOFTWARE_TO_BACKEND_TYPE,
    load_manifest,
)


# ---------------------------------------------------------------------------
# SOFTWARE_TO_BACKEND_TYPE mapping
# ---------------------------------------------------------------------------


class TestSoftwareMapping:
    def test_llamacpp_maps_to_llama_cpp(self):
        assert SOFTWARE_TO_BACKEND_TYPE["llamacpp"] == "llama-cpp"

    def test_embed_maps_to_llama_cpp(self):
        assert SOFTWARE_TO_BACKEND_TYPE["embed"] == "llama-cpp"

    def test_kokoro_maps_to_kokoro(self):
        assert SOFTWARE_TO_BACKEND_TYPE["kokoro"] == "kokoro"

    def test_whisper_maps_to_whisper(self):
        assert SOFTWARE_TO_BACKEND_TYPE["whisper"] == "whisper"


# ---------------------------------------------------------------------------
# load_manifest — file-based tests
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_returns_empty_on_missing_file(self):
        """When the manifest file does not exist, return an empty manifest."""
        result = load_manifest("/nonexistent/path/worker-models.json")
        assert result == {"resource_id": "", "models": []}

    def test_parses_valid_manifest(self):
        """A valid JSON manifest is parsed correctly."""
        manifest = {
            "resource_id": "worker-1:gpu-cuda-0",
            "models": [
                {
                    "model_id": "qwen3-embedding-8b",
                    "capability": "embed",
                    "software": "embed",
                    "port": 8080,
                    "vram_required_gb": 8.0,
                    "health_url": "http://localhost:8080/health",
                },
                {
                    "model_id": "kokoro-v1.0",
                    "capability": "tts",
                    "software": "kokoro",
                    "port": 8880,
                    "vram_required_gb": 0.5,
                    "health_url": "http://localhost:8880/health",
                },
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(manifest, f)
            tmp_path = f.name

        try:
            result = load_manifest(tmp_path)
            assert result["resource_id"] == "worker-1:gpu-cuda-0"
            assert len(result["models"]) == 2
            assert result["models"][0]["model_id"] == "qwen3-embedding-8b"
            assert result["models"][1]["model_id"] == "kokoro-v1.0"
        finally:
            os.unlink(tmp_path)

    def test_manifest_with_empty_models(self):
        """A manifest with an empty models list."""
        manifest = {"resource_id": "worker-1:gpu-cuda-0", "models": []}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(manifest, f)
            tmp_path = f.name

        try:
            result = load_manifest(tmp_path)
            assert result["resource_id"] == "worker-1:gpu-cuda-0"
            assert result["models"] == []
        finally:
            os.unlink(tmp_path)

    def test_invalid_json_raises(self):
        """Invalid JSON raises json.JSONDecodeError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("not valid json {{{")
            tmp_path = f.name

        try:
            with pytest.raises(json.JSONDecodeError):
                load_manifest(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_env_var_overrides_path(self, monkeypatch):
        """TAOS_WORKER_MANIFEST env var overrides the default path."""
        manifest = {"resource_id": "custom-path", "models": []}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(manifest, f)
            tmp_path = f.name

        try:
            monkeypatch.setenv("TAOS_WORKER_MANIFEST", tmp_path)
            result = load_manifest()
            assert result["resource_id"] == "custom-path"
        finally:
            os.unlink(tmp_path)

    def test_explicit_path_overrides_env_var(self, monkeypatch):
        """Explicit path argument wins over the env var."""
        manifest_env = {"resource_id": "from-env", "models": []}
        manifest_arg = {"resource_id": "from-arg", "models": []}

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(manifest_env, f)
            env_path = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(manifest_arg, f)
            arg_path = f.name

        try:
            monkeypatch.setenv("TAOS_WORKER_MANIFEST", env_path)
            result = load_manifest(arg_path)
            assert result["resource_id"] == "from-arg"
        finally:
            os.unlink(env_path)
            os.unlink(arg_path)

    def test_default_path_fallback(self, monkeypatch):
        """Without env var or explicit path, DEFAULT_MANIFEST_PATH is used."""
        monkeypatch.delenv("TAOS_WORKER_MANIFEST", raising=False)
        # The default path won't exist in test, so we get empty manifest.
        result = load_manifest()
        assert result == {"resource_id": "", "models": []}
        # Verify the constant is what we expect.
        assert DEFAULT_MANIFEST_PATH == "/etc/taos/worker-models.json"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestLoadManifestEdgeCases:
    def test_empty_file_raises(self):
        """An empty file is not valid JSON."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("")
            tmp_path = f.name

        try:
            with pytest.raises(json.JSONDecodeError):
                load_manifest(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_missing_model_id_handled(self):
        """Model entries missing model_id are still parsed."""
        manifest = {
            "resource_id": "w1",
            "models": [
                {"capability": "chat", "software": "llamacpp"},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(manifest, f)
            tmp_path = f.name

        try:
            result = load_manifest(tmp_path)
            assert len(result["models"]) == 1
            # load_manifest doesn't validate individual fields —
            # that's the enrichment layer's job.
        finally:
            os.unlink(tmp_path)

    def test_extra_fields_preserved(self):
        """Unknown fields in the manifest are passed through."""
        manifest = {
            "resource_id": "w1",
            "models": [{"model_id": "test", "extra_field": "surprise"}],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(manifest, f)
            tmp_path = f.name

        try:
            result = load_manifest(tmp_path)
            assert "extra_field" in result["models"][0]
            assert result["models"][0]["extra_field"] == "surprise"
        finally:
            os.unlink(tmp_path)
