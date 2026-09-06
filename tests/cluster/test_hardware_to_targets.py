"""Hardware → catalog targets enum derivation.

Tests cover the realistic device classes the catalog supports today:
Pi NPU, Mac M-series, Linux+CUDA, Linux+Vulkan, CPU-only fallback.
"""
import pytest

from tinyagentos.cluster.capabilities import hardware_to_targets


class TestHardwareToTargets:
    def test_rk3588_npu_returns_rockchip_and_cpu(self):
        hw = {
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "rk3588"},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["rockchip", "cpu"]

    def test_rknpu_kernel_module_name_returns_rockchip_and_cpu(self):
        # Real Pi production hardware.json reports npu.type="rknpu" (the
        # kernel module name) rather than "rk3588" (the chip generation).
        # Both must map to the rockchip catalog target.
        hw = {
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "rknpu", "tops": 6, "cores": 3},
            "gpu": {"type": "mali", "vulkan": True},
            "ram_mb": 15958,
        }
        assert hardware_to_targets(hw) == ["rockchip", "cpu"]

    def test_apple_silicon_returns_apple_and_cpu(self):
        hw = {
            "cpu": {"arch": "arm64"},
            "gpu": {"type": "apple"},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["apple-silicon", "cpu"]

    def test_nvidia_cuda_returns_x86_cuda_and_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288},
            "ram_mb": 32768,
        }
        assert hardware_to_targets(hw) == ["x86-cuda", "cpu"]

    def test_amd_vulkan_returns_x86_vulkan_and_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "amd", "vulkan": True, "vram_mb": 8192},
            "ram_mb": 32768,
        }
        assert hardware_to_targets(hw) == ["x86-vulkan", "cpu"]

    def test_intel_vulkan_returns_x86_vulkan_and_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "intel", "vulkan": True, "vram_mb": 4096},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["x86-vulkan", "cpu"]

    def test_mali_vulkan_returns_arm_vulkan_and_cpu(self):
        # ARM Mali (Pi-class), Adreno (Snapdragon) — Vulkan is cross-vendor
        # so an ARM device with a Vulkan-capable GPU should claim arm-vulkan.
        hw = {
            "cpu": {"arch": "aarch64"},
            "gpu": {"type": "mali", "vulkan": True, "vram_mb": 0},
            "ram_mb": 8192,
        }
        assert hardware_to_targets(hw) == ["arm-vulkan", "cpu"]

    def test_jetson_vulkan_without_cuda_returns_arm_vulkan(self):
        # NVIDIA Jetson with Vulkan exposed but CUDA flag missing — picked
        # up via the cross-vendor Vulkan branch and emits arm-vulkan.
        hw = {
            "cpu": {"arch": "aarch64"},
            "gpu": {"type": "nvidia", "vulkan": True, "vram_mb": 8192},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["arm-vulkan", "cpu"]

    def test_cpu_only_returns_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "ram_mb": 8192,
        }
        assert hardware_to_targets(hw) == ["cpu"]

    def test_arm_cpu_only_returns_cpu(self):
        hw = {
            "cpu": {"arch": "aarch64"},
            "ram_mb": 4096,
        }
        assert hardware_to_targets(hw) == ["cpu"]

    def test_empty_hardware_returns_cpu_only(self):
        assert hardware_to_targets({}) == ["cpu"]

    def test_npu_takes_priority_over_gpu(self):
        # If both NPU and GPU are present, NPU wins (we run accelerated
        # there first), but cpu still in the list as the fallback.
        hw = {
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "rk3588"},
            "gpu": {"type": "mali"},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["rockchip", "cpu"]

    def test_string_cpu_field_does_not_crash(self):
        # Older worker agents may send cpu as a plain string, not a dict.
        hw = {"cpu": "x86_64", "ram_mb": 8192}
        assert hardware_to_targets(hw) == ["cpu"]
