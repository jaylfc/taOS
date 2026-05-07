"""Tests for worker_tier_id RAM-bucket snapping.

Catalog manifests only declare canonical RAM sizes (2/4/8/16/32/64/128 GB).
Devices report slightly lower values after kernel reservation (e.g. 15 GB on a
16 GB Pi), so tier_id must snap UP to the next bucket.
"""
import pytest

from tinyagentos.cluster.capabilities import worker_tier_id, _snap_to_bucket


class TestSnapToBucket:
    def test_exact_bucket_unchanged(self):
        assert _snap_to_bucket(16) == 16

    def test_4gb_exact(self):
        assert _snap_to_bucket(4) == 4

    def test_7gb_snaps_to_8(self):
        assert _snap_to_bucket(7) == 8

    def test_15gb_snaps_to_16(self):
        assert _snap_to_bucket(15) == 16

    def test_17gb_snaps_to_32(self):
        assert _snap_to_bucket(17) == 32

    def test_33gb_snaps_to_64(self):
        assert _snap_to_bucket(33) == 64

    def test_200gb_clamps_to_128(self):
        assert _snap_to_bucket(200) == 128

    def test_zero_clamps_to_2(self):
        # raw_gb = max(1, 0) = 1 → first bucket ≥ 1 is 2
        assert _snap_to_bucket(0) == 2

    def test_1gb_snaps_to_2(self):
        assert _snap_to_bucket(1) == 2


class TestWorkerTierIdPiBucket:
    """Verify that a 15 958 MB Pi (16 GB after kernel reservation) maps to
    arm-npu-16gb, not arm-npu-15gb."""

    def test_pi_npu_15gb_raw_returns_16gb_tier(self):
        hw = {
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "rknpu", "tops": 6, "cores": 3},
            "ram_mb": 15000,
        }
        tier = worker_tier_id(hw)
        assert tier.endswith("-16gb"), f"expected -16gb suffix, got {tier!r}"
        assert tier == "arm-npu-16gb"

    def test_pi_npu_exact_16gb_returns_16gb_tier(self):
        hw = {
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "rk3588"},
            "ram_mb": 16384,
        }
        assert worker_tier_id(hw) == "arm-npu-16gb"

    def test_pi_cpu_only_15gb_raw_returns_16gb_tier(self):
        hw = {
            "cpu": {"arch": "aarch64"},
            "ram_mb": 15000,
        }
        assert worker_tier_id(hw) == "arm-cpu-16gb"

    def test_cuda_12gb_vram_exact_stays_12(self):
        # 12 GB is not a canonical bucket — snaps up to 16
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288},
            "ram_mb": 32768,
        }
        tier = worker_tier_id(hw)
        assert tier == "x86-cuda-16gb"

    def test_cuda_8gb_vram_exact_stays_8(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 8192},
            "ram_mb": 32768,
        }
        assert worker_tier_id(hw) == "x86-cuda-8gb"
