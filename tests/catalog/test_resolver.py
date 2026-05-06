"""Behavior tests for resolve() and classify().

Manifest fixtures are dicts (matching what AppManifest.from_file produces
for the .install/.requires/.variants slots), not full AppManifest objects —
the resolver only needs the variants list and never touches the rest.
"""
import pytest

from tinyagentos.catalog.resolver import (
    DeviceCapability,
    ResolveErr,
    ResolveOk,
    resolve,
)


def make_qwen_manifest() -> dict:
    """Realistic post-migration model manifest fixture."""
    return {
        "id": "qwen2.5-3b",
        "type": "model",
        "context_window": 32768,
        "variants": [
            {
                "id": "q4_k_m",
                "size_mb": 1900,
                "requires": {
                    "backends": [
                        {
                            "id": "rk-llama-cpp",
                            "targets": ["rockchip-rk3588"],
                            "min_ram_mb": 4096,
                        },
                        {
                            "id": "ollama",
                            "targets": ["apple-silicon", "x86-cuda", "cpu"],
                            "min_ram_mb": 4096,
                        },
                        {
                            "id": "llama-cpp",
                            "targets": ["cpu"],
                            "min_ram_mb": 4096,
                        },
                    ],
                },
            },
            {
                "id": "q8_0",
                "size_mb": 3400,
                "requires": {
                    "backends": [
                        {
                            "id": "rk-llama-cpp",
                            "targets": ["rockchip-rk3588"],
                            "min_ram_mb": 6144,
                        },
                        {
                            "id": "ollama",
                            "targets": ["apple-silicon", "x86-cuda", "cpu"],
                            "min_ram_mb": 6144,
                        },
                    ],
                },
            },
        ],
    }


def pi_device(installed: tuple[str, ...] = ()) -> DeviceCapability:
    return DeviceCapability(
        device_id="pi",
        targets=("rockchip-rk3588", "cpu"),
        total_ram_mb=16384,
        total_vram_mb=0,
        free_disk_mb=50_000,
        installed_backends=installed,
    )


class TestResolveExplicitVariant:
    def test_use_when_backend_already_installed(self):
        m = make_qwen_manifest()
        r = resolve(m, "q4_k_m", pi_device(installed=("rk-llama-cpp",)))
        assert isinstance(r, ResolveOk)
        assert r.backend_id == "rk-llama-cpp"
        assert r.variant_id == "q4_k_m"
        assert r.action == "use"

    def test_install_chain_when_backend_missing(self):
        m = make_qwen_manifest()
        r = resolve(m, "q4_k_m", pi_device(installed=()))
        assert isinstance(r, ResolveOk)
        assert r.backend_id == "rk-llama-cpp"
        assert r.action == "install_chain"

    def test_declaration_order_tiebreaker(self):
        # Pi has both rockchip and cpu in targets — rk-llama-cpp is declared
        # first; even if llama-cpp would also match cpu, the first match wins.
        m = make_qwen_manifest()
        r = resolve(m, "q4_k_m", pi_device(installed=("rk-llama-cpp", "llama-cpp")))
        assert isinstance(r, ResolveOk)
        assert r.backend_id == "rk-llama-cpp"


class TestResolveGates:
    def test_no_target_intersection_returns_err(self):
        no_match = {
            "id": "rkllm-only-model",
            "type": "model",
            "variants": [
                {
                    "id": "default",
                    "size_mb": 1000,
                    "requires": {
                        "backends": [
                            {
                                "id": "rkllama",
                                "targets": ["rockchip-rk3588"],
                                "min_ram_mb": 4096,
                            },
                        ],
                    },
                },
            ],
        }
        mac = DeviceCapability(
            device_id="mac",
            targets=("apple-silicon", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=200_000,
            installed_backends=(),
        )
        r = resolve(no_match, "default", mac)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "target"

    def test_ram_short_returns_err(self):
        m = make_qwen_manifest()
        small_pi = DeviceCapability(
            device_id="pi",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=2048,  # below q8_0's 6144 floor
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        r = resolve(m, "q8_0", small_pi)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "ram"
        assert r.near_miss["short_by_mb"] > 0

    def test_vram_short_returns_err(self):
        vram_required = {
            "id": "vram-hungry",
            "type": "model",
            "variants": [
                {
                    "id": "fp16",
                    "size_mb": 1000,
                    "requires": {
                        "backends": [
                            {
                                "id": "vllm",
                                "targets": ["x86-cuda"],
                                "min_ram_mb": 4096,
                                "min_vram_mb": 24576,
                            },
                        ],
                    },
                },
            ],
        }
        small_gpu = DeviceCapability(
            device_id="gpu",
            targets=("x86-cuda", "cpu"),
            total_ram_mb=32768,
            total_vram_mb=8192,
            free_disk_mb=200_000,
            installed_backends=(),
        )
        r = resolve(vram_required, "fp16", small_gpu)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "vram"

    def test_disk_short_returns_err_even_when_other_gates_pass(self):
        m = make_qwen_manifest()
        full_pi = DeviceCapability(
            device_id="pi",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=500,  # well below the 1900 MB the variant needs
            installed_backends=("rk-llama-cpp",),
        )
        r = resolve(m, "q4_k_m", full_pi)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "disk"
        assert r.near_miss["short_by_mb"] >= 1400


class TestResolveUnknownVariant:
    def test_returns_err_when_variant_id_not_found(self):
        m = make_qwen_manifest()
        r = resolve(m, "does-not-exist", pi_device())
        assert isinstance(r, ResolveErr)
        assert "does-not-exist" in r.reason
