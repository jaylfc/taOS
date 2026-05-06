"""Tests for the manifest migration script.

The script is one-shot but worth pinning while it runs against the real
catalog in Task 9 — bugs in inference would silently corrupt 30 manifests.
"""
import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

# Load the migration script as a module without making it part of the
# tinyagentos package — keeps it self-contained.
SCRIPT = Path("scripts/migrate-manifests-to-requires-backends.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_manifests", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migrate_mod():
    return _load_module()


class TestInferBackends:
    def test_rkllama_method_maps_to_rkllama_backend(self, migrate_mod):
        manifest = {
            "id": "qwen2.5-3b-rkllm",
            "type": "model",
            "install": {"method": "rkllama"},
            "variants": [{"id": "default", "min_ram_mb": 4096}],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        assert out == [
            {"id": "rkllama", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096}
        ]

    def test_rkllamacpp_method_maps_to_rk_llama_cpp_backend(self, migrate_mod):
        manifest = {
            "type": "model",
            "install": {"method": "rkllamacpp"},
            "variants": [{"id": "q4_k_m", "min_ram_mb": 4096}],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        assert out == [
            {"id": "rk-llama-cpp", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096}
        ]

    def test_variant_backend_ollama_llama_cpp_maps_to_pair(self, migrate_mod):
        manifest = {
            "type": "model",
            "variants": [
                {
                    "id": "q4_k_m",
                    "min_ram_mb": 4096,
                    "backend": ["ollama", "llama-cpp"],
                },
            ],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        ids = [b["id"] for b in out]
        assert "ollama" in ids
        assert "llama-cpp" in ids
        for b in out:
            if b["id"] == "ollama":
                assert "apple-silicon" in b["targets"]
                assert "x86-cuda" in b["targets"]
                assert "cpu" in b["targets"]
            if b["id"] == "llama-cpp":
                # llama-cpp can run on cpu and on Vulkan-capable GPUs (both arches).
                assert "cpu" in b["targets"]

    def test_mlx_only_maps_to_apple_silicon(self, migrate_mod):
        manifest = {
            "type": "model",
            "variants": [{"id": "fp16", "min_ram_mb": 8192, "backend": ["mlx"]}],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        assert out == [
            {"id": "mlx", "targets": ["apple-silicon"], "min_ram_mb": 8192}
        ]


class TestRewriteManifest:
    def test_removes_deprecated_fields_and_adds_requires_backends(self, migrate_mod, tmp_path: Path):
        src = dedent(
            """
            id: qwen2.5-3b
            name: Qwen 2.5 3B Instruct
            type: model
            version: 2.5.0
            capabilities: [chat, tool-calling]
            variants:
              - id: q4_k_m
                size_mb: 1900
                min_ram_mb: 3072
                download_url: https://example/q4.gguf
                backend: [ollama, llama-cpp]
            hardware_tiers:
              cpu-only: {recommended: q4_k_m}
            """
        ).strip() + "\n"
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(src)

        migrate_mod.migrate_manifest(manifest_path, context_lookup={"qwen2.5-3b": 32768})

        out = yaml.safe_load(manifest_path.read_text())
        assert "install" not in out
        assert "backend" not in out["variants"][0]
        assert out["context_window"] == 32768
        deps = out["variants"][0]["requires"]["backends"]
        assert any(b["id"] == "ollama" for b in deps)
        assert any(b["id"] == "llama-cpp" for b in deps)
        # hardware_tiers is preserved as opaque metadata.
        assert "hardware_tiers" in out

    def test_skips_non_model_manifests(self, migrate_mod, tmp_path: Path):
        src = dedent(
            """
            id: some-service
            name: A Service
            type: service
            version: 1.0.0
            install: {method: docker, image: foo/bar}
            """
        ).strip() + "\n"
        p = tmp_path / "manifest.yaml"
        p.write_text(src)
        before = p.read_text()
        migrate_mod.migrate_manifest(p, context_lookup={})
        assert p.read_text() == before  # unchanged
