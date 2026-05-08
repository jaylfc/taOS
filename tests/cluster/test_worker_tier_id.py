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

    def test_17gb_snaps_to_24(self):
        # With the 24gb bucket added (RTX 3090/4090), 17gb no longer
        # leaps straight to 32gb.
        assert _snap_to_bucket(17) == 24

    def test_33gb_snaps_to_64(self):
        assert _snap_to_bucket(33) == 64

    def test_5gb_snaps_to_6(self):
        # Orange Pi 5 6 GB used to snap to 8gb-tier and miss every
        # arm-npu-6gb manifest.
        assert _snap_to_bucket(5) == 6

    def test_12gb_exact(self):
        # RTX 3060 12 GB used to snap to 16gb-tier and miss every
        # x86-cuda-12gb manifest.
        assert _snap_to_bucket(12) == 12

    def test_24gb_exact(self):
        # RTX 3090 / 4090 used to snap to 32gb and miss every
        # x86-cuda-24gb manifest.
        assert _snap_to_bucket(24) == 24

    def test_13gb_snaps_to_16(self):
        # In-between values still round up to the next bucket.
        assert _snap_to_bucket(13) == 16

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

    def test_cuda_12gb_vram_lands_on_12gb_tier(self):
        # 12 GB IS a canonical bucket now — was wrongly snapping to 16
        # before the bucket fix in this PR. RTX 3060 12 GB / RTX 4060 Ti
        # 12 GB / many other consumer cards all sit at exactly 12 GB,
        # and 30 catalog manifests target x86-cuda-12gb.
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288},
            "ram_mb": 32768,
        }
        tier = worker_tier_id(hw)
        assert tier == "x86-cuda-12gb"

    def test_cuda_8gb_vram_exact_stays_8(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 8192},
            "ram_mb": 32768,
        }
        assert worker_tier_id(hw) == "x86-cuda-8gb"
