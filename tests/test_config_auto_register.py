from __future__ import annotations
import pytest
from pathlib import Path
from tinyagentos.config import auto_register_from_manifest, AppConfig


def test_auto_register_adds_backend(tmp_path: Path):
    """auto_register_from_manifest writes a backend entry from a manifest file."""
    manifest = tmp_path / "rknn-sd.yaml"
    manifest.write_text("""
id: rknn-sd
name: RKNN Stable Diffusion
type: rknn-sd
default_url: http://localhost:7863
capabilities:
  - image-generation
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-rknn-sd"
  stop_cmd: "systemctl stop tinyagentos-rknn-sd"
  startup_timeout_seconds: 90
""")
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config)
    assert added is True
    assert len(config.backends) == 1
    b = config.backends[0]
    assert b["name"] == "local-rknn-sd"
    assert b["type"] == "rknn-sd"
    assert b["url"] == "http://localhost:7863"
    assert b["priority"] == 99
    assert b["enabled"] is True
    assert b["auto_manage"] is True
    assert b["keep_alive_minutes"] == 10
    assert b["start_cmd"] == "systemctl start tinyagentos-rknn-sd"
    assert b["stop_cmd"] == "systemctl stop tinyagentos-rknn-sd"
    assert b["startup_timeout_seconds"] == 90


def test_auto_register_idempotent(tmp_path: Path):
    """Calling auto_register_from_manifest twice does not add duplicates."""
    manifest = tmp_path / "rknn-sd.yaml"
    manifest.write_text("""
id: rknn-sd
name: RKNN Stable Diffusion
type: rknn-sd
default_url: http://localhost:7863
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-rknn-sd"
  stop_cmd: "systemctl stop tinyagentos-rknn-sd"
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
id: rknn-stable-diffusion
name: RKNN Stable Diffusion
type: service
lifecycle:
  backend_type: rknn-sd
  default_url: http://localhost:7863
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-rknn-sd"
  stop_cmd: "systemctl stop tinyagentos-rknn-sd"
  startup_timeout_seconds: 90
""")
    config = AppConfig()
    added = auto_register_from_manifest(manifest, config)
    assert added is True
    assert len(config.backends) == 1
    b = config.backends[0]
    assert b["name"] == "local-rknn-stable-diffusion"
    assert b["type"] == "rknn-sd"
    assert b["url"] == "http://localhost:7863"
    assert b["auto_manage"] is True
    assert b["start_cmd"] == "systemctl start tinyagentos-rknn-sd"
    assert b["stop_cmd"] == "systemctl stop tinyagentos-rknn-sd"


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
