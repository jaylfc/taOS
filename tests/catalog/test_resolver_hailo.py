"""Hailo-10H end-to-end resolver coverage.

Loads a real HEF model manifest from app-catalog and resolves it against a
hardware profile built via hardware_to_targets(), the same path the store
install dispatcher uses. Catches the class of bug where a manifest's
requires.backends entry has no targets, so no device can ever resolve it.
"""
from pathlib import Path

import pytest
import yaml

from tinyagentos.catalog.resolver import DeviceCapability, ResolveErr, ResolveOk, resolve
from tinyagentos.cluster.capabilities import hardware_to_targets

_MODELS_DIR = Path(__file__).resolve().parents[2] / "app-catalog" / "models"


def _load_manifest(model_id: str) -> dict:
    path = _MODELS_DIR / model_id / "manifest.yaml"
    return yaml.safe_load(path.read_text())


def _pi5_hailo_hardware() -> dict:
    return {
        "cpu": {"arch": "aarch64"},
        "npu": {"type": "hailo10h", "tops": 40, "cores": 1},
        "ram_mb": 8192,
    }


def _x86_cpu_only_hardware() -> dict:
    return {
        "cpu": {"arch": "x86_64"},
        "ram_mb": 16384,
    }


class TestHailoManifestResolves:
    def test_pi5_hailo_resolves_qwen25_1_5b_hef_to_hailo_ollama(self):
        manifest = _load_manifest("qwen2.5-1.5b")
        targets = hardware_to_targets(_pi5_hailo_hardware())
        assert "hailo" in targets, (
            "Pi 5 + Hailo-10H hardware profile did not produce a 'hailo' "
            f"catalog target: {targets!r}"
        )
        device = DeviceCapability(
            device_id="pi5-hailo",
            targets=tuple(targets),
            total_ram_mb=8192,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        result = resolve(manifest, "a8w4", device)
        assert isinstance(result, ResolveOk), (
            f"expected ResolveOk on a Hailo-10H device, got {result!r}"
        )
        assert result.backend_id == "hailo-ollama"

    def test_cpu_only_x86_cannot_resolve_hailo_manifest(self):
        manifest = _load_manifest("qwen2.5-1.5b")
        targets = hardware_to_targets(_x86_cpu_only_hardware())
        device = DeviceCapability(
            device_id="x86-cpu-only",
            targets=tuple(targets),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        result = resolve(manifest, "a8w4", device)
        assert isinstance(result, ResolveErr), (
            f"expected ResolveErr on a CPU-only x86 device, got {result!r}"
        )


class TestHailoNewManifestsResolve:
    """Slice-S6 coverage: every new Hailo-10H .hef manifest resolves to hailo-ollama."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "qwen2.5-1.5b",
            "qwen2.5-coder-1.5b",
            "qwen2-1.5b",
            "llama-3.2-1b",
            "llama-3.2-3b",
            "qwen3",
            "deepseek-r1-1.5b",
        ],
    )
    def test_pi5_hailo_resolves_to_hailo_ollama(self, model_id):
        manifest = _load_manifest(model_id)
        targets = hardware_to_targets(_pi5_hailo_hardware())
        device = DeviceCapability(
            device_id="pi5-hailo",
            targets=tuple(targets),
            total_ram_mb=8192,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        result = resolve(manifest, "a8w4", device)
        assert isinstance(result, ResolveOk), (
            f"expected ResolveOk on a Hailo-10H device for {model_id}, got {result!r}"
        )
        assert result.backend_id == "hailo-ollama"

    @pytest.mark.parametrize(
        "model_id",
        [
            "qwen2.5-1.5b",
            "qwen2.5-coder-1.5b",
            "qwen2-1.5b",
            "llama-3.2-1b",
            "llama-3.2-3b",
            "qwen3",
            "deepseek-r1-1.5b",
        ],
    )
    def test_cpu_only_x86_cannot_resolve_new_hailo_manifest(self, model_id):
        manifest = _load_manifest(model_id)
        targets = hardware_to_targets(_x86_cpu_only_hardware())
        device = DeviceCapability(
            device_id="x86-cpu-only",
            targets=tuple(targets),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        result = resolve(manifest, "a8w4", device)
        assert isinstance(result, ResolveErr), (
            f"expected ResolveErr on a CPU-only x86 device for {model_id}, got {result!r}"
        )
