"""Tier-ladder matching tests.

A worker on a bigger numeric tier (e.g. ``x86-cuda-16gb``) should match
manifests that only declare a smaller minimum tier of the same arch and
accelerator (e.g. ``x86-cuda-12gb``). This stops manifest authors having
to repeat themselves across every bucket size — declare the minimum,
bigger machines inherit.
"""
from __future__ import annotations

import pytest

from tinyagentos.cluster.capabilities import (
    _parse_numeric_tier,
    tier_compatible,
)


class TestParseNumericTier:
    def test_x86_cuda(self):
        assert _parse_numeric_tier("x86-cuda-12gb") == ("x86", "cuda", 12)

    def test_arm_npu(self):
        assert _parse_numeric_tier("arm-npu-16gb") == ("arm", "npu", 16)

    def test_x86_vulkan(self):
        assert _parse_numeric_tier("x86-vulkan-8gb") == ("x86", "vulkan", 8)

    def test_apple_silicon_is_flat(self):
        assert _parse_numeric_tier("apple-silicon") is None

    def test_cpu_only_is_flat(self):
        assert _parse_numeric_tier("cpu-only") is None

    def test_garbage_returns_none(self):
        assert _parse_numeric_tier("") is None
        assert _parse_numeric_tier("not-a-tier") is None
        assert _parse_numeric_tier("x86-cuda-NOTANUMBERgb") is None


class TestTierCompatible:
    """Worker on the LEFT, manifest declaration on the RIGHT."""

    def test_exact_match(self):
        assert tier_compatible("x86-cuda-16gb", "x86-cuda-16gb")

    def test_bigger_worker_matches_smaller_manifest(self):
        # The point of the ladder: manifests declare minimums, bigger
        # workers inherit.
        assert tier_compatible("x86-cuda-16gb", "x86-cuda-12gb")
        assert tier_compatible("x86-cuda-24gb", "x86-cuda-12gb")
        assert tier_compatible("arm-npu-32gb", "arm-npu-16gb")

    def test_smaller_worker_does_not_match_bigger_manifest(self):
        # A 12 GB card cannot run a model that needs 24 GB.
        assert not tier_compatible("x86-cuda-12gb", "x86-cuda-24gb")
        assert not tier_compatible("arm-npu-16gb", "arm-npu-32gb")

    def test_different_arch_never_matches(self):
        # ARM and x86 builds are distinct artefacts even with same accel.
        assert not tier_compatible("x86-cuda-16gb", "arm-cuda-16gb")
        assert not tier_compatible("arm-vulkan-16gb", "x86-vulkan-16gb")

    def test_different_accel_never_matches(self):
        # Same card, different driver — runtime artefacts differ.
        assert not tier_compatible("x86-cuda-16gb", "x86-vulkan-16gb")
        assert not tier_compatible("x86-cuda-16gb", "x86-rocm-16gb")
        assert not tier_compatible("x86-cuda-16gb", "x86-cpu-16gb")

    def test_apple_silicon_only_matches_apple_silicon(self):
        # Flat tiers don't ladder.
        assert tier_compatible("apple-silicon", "apple-silicon")
        assert not tier_compatible("apple-silicon", "x86-cuda-16gb")
        assert not tier_compatible("x86-cuda-16gb", "apple-silicon")

    def test_cpu_only_only_matches_cpu_only(self):
        assert tier_compatible("cpu-only", "cpu-only")
        assert not tier_compatible("cpu-only", "x86-cuda-12gb")
        assert not tier_compatible("x86-cuda-16gb", "cpu-only")

    def test_unknown_tier_strings_only_exact_match(self):
        assert tier_compatible("garbage", "garbage")
        assert not tier_compatible("garbage", "x86-cuda-12gb")


class TestPotentialCapabilitiesLadder:
    """End-to-end via potential_capabilities() — the function the cluster
    UI calls. Build a fake registry with manifests at varying minimum
    tiers and check the tier ladder picks them up."""

    def _make_manifest(self, tier_id: str, capabilities: list[str]):
        from types import SimpleNamespace

        return SimpleNamespace(
            type="model",
            hardware_tiers={tier_id: {"recommended": "default"}},
            capabilities=capabilities,
        )

    def _registry(self, manifests: list):
        from types import SimpleNamespace

        return SimpleNamespace(
            list_available=lambda type_filter=None: [
                m for m in manifests if type_filter is None or m.type == type_filter
            ],
        )

    def test_x86_cuda_16gb_picks_up_12gb_manifest(self):
        from tinyagentos.cluster.capabilities import potential_capabilities

        registry = self._registry(
            [
                self._make_manifest("x86-cuda-12gb", ["chat", "code"]),
                self._make_manifest("x86-cuda-24gb", ["vision"]),
            ]
        )
        # Fedora-style hardware: 12 GB RTX 3060 → x86-cuda-12gb after
        # the bucket fix in #435 lands; or x86-cuda-16gb pre-merge.
        # Either way the 12gb manifest has to come along for the ride.
        # Use a 16gb tier explicitly here to verify the ladder path
        # independently of bucket coverage.
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16 * 1024},
        }
        tier_id, caps = potential_capabilities(hw, registry)
        assert tier_id == "x86-cuda-16gb"
        # 12gb manifest qualifies (smaller minimum); 24gb does not.
        assert "chat" in caps and "code" in caps
        assert "vision" not in caps
