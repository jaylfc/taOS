from __future__ import annotations
import pytest
from pathlib import Path
from tinyagentos.config import auto_register_from_manifest, AppConfig


def test_auto_register_adds_backend(tmp_path: Path):
    """auto_register_from_manifest writes a backend entry from a manifest file."""
    manifest = tmp_path / "sd-cpp.yaml"
    manifest.write_text("""
id: sd-cpp
name: Stable Diffusion CPP
type: sd-cpp
default_url: http://localhost:7864
capabilities:
  - image-generation
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-sdcpp"
  stop_cmd: "systemctl stop tinyagentos-sdcpp"
  startup_timeout_seconds: 90
""")
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config)
    assert added is True
    assert len(config.backends) == 1
    b = config.backends[0]
    assert b["name"] == "local-sd-cpp"
    assert b["type"] == "sd-cpp"
    assert b["url"] == "http://localhost:7864"
    assert b["priority"] == 99
    assert b["enabled"] is True
    assert b["auto_manage"] is True
    assert b["keep_alive_minutes"] == 10
    assert b["start_cmd"] == "systemctl start tinyagentos-sdcpp"
    assert b["stop_cmd"] == "systemctl stop tinyagentos-sdcpp"
    assert b["startup_timeout_seconds"] == 90


def test_auto_register_idempotent(tmp_path: Path):
    """Calling auto_register_from_manifest twice does not add duplicates."""
    manifest = tmp_path / "sd-cpp.yaml"
    manifest.write_text("""
id: sd-cpp
name: Stable Diffusion CPP
type: sd-cpp
default_url: http://localhost:7864
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-sdcpp"
  stop_cmd: "systemctl stop tinyagentos-sdcpp"
  startup_timeout_seconds: 90
""")
    config = AppConfig()
    assert auto_register_from_manifest(manifest, config) is True
    assert auto_register_from_manifest(manifest, config) is False
    assert len(config.backends) == 1


def test_auto_register_catalog_manifest_format(tmp_path: Path):
    """auto_register_from_manifest works with catalog manifests that use
    type: service with lifecycle.backend_type and lifecycle.default_url."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("""
id: stable-diffusion-cpp
name: Stable Diffusion CPP
type: service
lifecycle:
  backend_type: sd-cpp
  default_url: http://localhost:7864
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-sdcpp"
  stop_cmd: "systemctl stop tinyagentos-sdcpp"
  startup_timeout_seconds: 90
""")
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config)
    assert added is True
    assert len(config.backends) == 1
    b = config.backends[0]
    assert b["name"] == "local-stable-diffusion-cpp"
    assert b["type"] == "sd-cpp"
    assert b["url"] == "http://localhost:7864"
    assert b["auto_manage"] is True
    assert b["start_cmd"] == "systemctl start tinyagentos-sdcpp"
    assert b["stop_cmd"] == "systemctl stop tinyagentos-sdcpp"


def test_auto_register_keep_alive_zero(tmp_path: Path):
    """keep_alive_minutes: 0 (always on) must be preserved — not treated as falsy."""
    manifest = tmp_path / "rkllama.yaml"
    manifest.write_text("""
id: rkllama
name: rkllama
type: rkllama
default_url: http://localhost:8080
lifecycle:
  auto_manage: true
  keep_alive_minutes: 0
  start_cmd: "systemctl start rkllama"
  stop_cmd: "systemctl stop rkllama"
""")
    config = AppConfig()
    auto_register_from_manifest(manifest, config)
    b = config.backends[0]
    assert b["keep_alive_minutes"] == 0, "0 means always-on; must not fall back to 10"


def test_auto_register_skips_unknown_backend_type(tmp_path: Path, caplog):
    """A manifest with a backend_type the controller doesn't have an
    adapter for must NOT register the backend — otherwise every health
    check round raises ValueError and /api/backends 500s. The fix is
    to skip + log loudly at registration time.
    """
    import logging

    # Catalog-format manifest with an invented backend type.
    manifest = tmp_path / "bogus.yaml"
    manifest.write_text("""
id: bogus-runtime
name: Bogus Runtime
type: service
lifecycle:
  backend_type: not-a-real-adapter
  default_url: http://localhost:9999
""")
    config = AppConfig()
    with caplog.at_level(logging.WARNING):
        added = auto_register_from_manifest(manifest, config)

    assert added is False
    assert config.backends == []
    # The warning has to be loud enough to be noticed in the controller
    # log — the user shouldn't have to grep the source to understand why
    # their service isn't registering.
    assert any(
        "not-a-real-adapter" in rec.message and "VALID_BACKEND_TYPES" in rec.message
        for rec in caplog.records
    ), f"expected loud warning; got: {[r.message for r in caplog.records]}"


def test_auto_register_flat_format_unknown_type_skipped(tmp_path: Path, caplog):
    """Same skip behaviour for flat-format manifests (top-level type is
    the backend type)."""
    import logging

    manifest = tmp_path / "flat-bogus.yaml"
    manifest.write_text("""
id: flat-bogus
name: Flat Bogus
type: not-a-real-adapter
default_url: http://localhost:9999
""")
    config = AppConfig()
    with caplog.at_level(logging.WARNING):
        added = auto_register_from_manifest(manifest, config)
    assert added is False
    assert config.backends == []


class _FakeHardwareProfile:
    """Test stub mirroring the HardwareProfile API the gate uses."""

    def __init__(self, hardware: dict):
        self.hardware = hardware


def test_auto_register_skips_when_hardware_only_lists_unsupported_tiers(tmp_path: Path, caplog):
    """rk-llama.cpp on x86: every CUDA / vulkan tier that matches the
    controller's tier_id is marked ``unsupported``, so the entry must be
    skipped rather than registered. johny saw rk-llama.cpp showing as
    installed on his x86 cluster — exactly this gap."""
    import logging

    manifest = tmp_path / "rk-llama-cpp.yaml"
    manifest.write_text("""
id: rk-llama-cpp
name: rk-llama.cpp
type: service
lifecycle:
  backend_type: openai-compatible
  default_url: http://localhost:8090
hardware_tiers:
  arm-npu-16gb: full
  arm-npu-32gb: full
  x86-cuda-12gb: unsupported
  x86-vulkan-8gb: unsupported
  cpu-only: unsupported
""")
    # x86 controller with a 12 GB CUDA card — tier_id = x86-cuda-12gb,
    # which IS in the manifest but as "unsupported". Skip.
    hw = _FakeHardwareProfile({
        "cpu": {"arch": "x86_64"},
        "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12 * 1024},
    })
    config = AppConfig()
    with caplog.at_level(logging.INFO):
        added = auto_register_from_manifest(manifest, config, hardware_profile=hw)
    assert added is False
    assert config.backends == []
    assert any(
        "doesn't match" in rec.message.lower() or "incompatible" in rec.message.lower()
        for rec in caplog.records
    )


def test_auto_register_accepts_when_compatible_tier_present(tmp_path: Path):
    """rk-llama.cpp on a Pi: arm-npu-16gb is declared ``full`` and matches
    the controller's tier_id, so the entry registers."""
    manifest = tmp_path / "rk-llama-cpp.yaml"
    manifest.write_text("""
id: rk-llama-cpp
name: rk-llama.cpp
type: service
lifecycle:
  backend_type: openai-compatible
  default_url: http://localhost:8090
hardware_tiers:
  arm-npu-16gb: full
  arm-npu-32gb: full
  x86-cuda-12gb: unsupported
  cpu-only: unsupported
""")
    hw = _FakeHardwareProfile({
        "cpu": {"arch": "aarch64"},
        "npu": {"type": "rk3588", "tops": 6, "cores": 3},
        "ram_mb": 16 * 1024,
    })
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config, hardware_profile=hw)
    assert added is True
    assert len(config.backends) == 1
    assert config.backends[0]["name"] == "local-rk-llama-cpp"


def test_auto_register_accepts_via_ladder_match(tmp_path: Path):
    """Bigger worker inherits smaller minimum tier (per #436's tier ladder).
    A manifest declaring only ``x86-cuda-8gb: full`` should register on
    a 16 GB CUDA worker."""
    manifest = tmp_path / "fake-svc.yaml"
    manifest.write_text("""
id: fake-svc
name: Fake Service
type: service
lifecycle:
  backend_type: openai-compatible
  default_url: http://localhost:9000
hardware_tiers:
  x86-cuda-8gb: full
""")
    hw = _FakeHardwareProfile({
        "cpu": {"arch": "x86_64"},
        "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 16 * 1024},
    })
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config, hardware_profile=hw)
    assert added is True


def test_auto_register_no_hardware_tiers_block_still_registers(tmp_path: Path):
    """Manifests without a hardware_tiers block (some infrastructure
    services) shouldn't be gated — the check is opt-in. Backwards
    compatible with existing manifests."""
    manifest = tmp_path / "infra-svc.yaml"
    manifest.write_text("""
id: infra-svc
name: Infra Service
type: service
lifecycle:
  backend_type: openai-compatible
  default_url: http://localhost:7000
""")
    hw = _FakeHardwareProfile({
        "cpu": {"arch": "x86_64"},
        "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12 * 1024},
    })
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config, hardware_profile=hw)
    assert added is True


def test_auto_register_without_hardware_profile_works_unchanged(tmp_path: Path):
    """Existing callers that don't pass hardware_profile must still work
    (no gate applied) — keeps the function backwards-compatible for
    tests and any non-controller call sites."""
    manifest = tmp_path / "rkllama.yaml"
    manifest.write_text("""
id: rkllama
name: rkllama
type: rkllama
default_url: http://localhost:8080
""")
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config)  # no hardware_profile kwarg
    assert added is True
