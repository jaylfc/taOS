#!/usr/bin/env python3
"""Tests for tinyagentos/orb/apps_store."""

import platform
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tinyagentos.orb.frames import OrbError
from tinyagentos.orb import apps_store


# ------------------------------------------------------------------ constants ----
def test_MANIFEST_KIND_constant():
    assert apps_store.MANIFEST_KIND == "orb-desktop-apps"


def test_PLATFORMS_constant():
    assert apps_store.PLATFORMS == (
        "macos-arm64", "macos-x64", "linux-x64", "linux-arm64", "windows-x64"
    )


# ------------------------------------------------------------------ exceptions ----
def test_all_exceptions_are_orb_error_subclasses():
    """Every new exception is an OrbError subclass."""
    for exc_name in [
        "DesktopManifestKindError",
        "DesktopManifestInvalid",
        "PlatformUnsupported",
    ]:
        exc_cls = getattr(apps_store, exc_name)
        assert issubclass(exc_cls, OrbError), f"{exc_name} must be OrbError subclass"


# ------------------------------------------------------------------ default_manifest_state_path ----
def test_default_manifest_state_path_returns_orb_subdir(tmp_path):
    """default_manifest_state_path returns data_dir / 'orb' / 'desktop_manifest_state.json'."""
    state_path = apps_store.default_manifest_state_path(tmp_path)
    assert state_path == tmp_path / "orb" / "desktop_manifest_state.json"


# ------------------------------------------------------------------ current_platform ----
@pytest.mark.parametrize("system,machine,expected", [
    ("Darwin", "arm64", "macos-arm64"),
    ("Linux", "x86_64", "linux-x64"),
    ("Linux", "aarch64", "linux-arm64"),
    ("Windows", "AMD64", "windows-x64"),
])
def test_current_platform_supported(system, machine, expected):
    """current_platform maps known system/machine to expected platform key."""
    assert apps_store.current_platform(system, machine) == expected


@pytest.mark.parametrize("system,machine", [
    ("FreeBSD", "amd64"),
    ("Linux", "riscv64"),
])
def test_current_platform_unsupported_raises(system, machine):
    """Unsupported system/machine raises PlatformUnsupported."""
    with pytest.raises(apps_store.PlatformUnsupported) as exc:
        apps_store.current_platform(system, machine)
    assert system in str(exc.value)
    assert machine in str(exc.value)


# ------------------------------------------------------------------ validate_relative_url ----
def test_validate_relative_url_accepted():
    """Valid relative URL is returned unchanged."""
    url = "openharness/1.0/oh-macos-arm64.zip"
    assert apps_store.validate_relative_url(url) == url


@pytest.mark.parametrize("bad_url", [
    "https://evil.example/x",
    "//evil.example/x",
    "/abs/x",
    "../x",
    "a/../x",
    "a//x",
    r"a\x",
    "x?y",
    "x#y",
    "",
])
def test_validate_relative_url_refused(bad_url):
    """Various bad relative URLs raise DesktopManifestInvalid."""
    with pytest.raises(apps_store.DesktopManifestInvalid):
        apps_store.validate_relative_url(bad_url)


# ------------------------------------------------------------------ resolve_url ----
def test_resolve_url_valid():
    """Valid base_url and rel -> joined URL."""
    base = "https://dl.example/orb/"
    rel = "a/b.zip"
    assert apps_store.resolve_url(base, rel) == "https://dl.example/orb/a/b.zip"


def test_resolve_url_http_base_refused():
    """http:// base raises DesktopManifestInvalid."""
    with pytest.raises(apps_store.DesktopManifestInvalid):
        apps_store.resolve_url("http://example.com/", "a/b.zip")


# ------------------------------------------------------------------ validate_manifest ----
def make_valid_manifest():
    """Valid manifest fixture."""
    return {
        "kind": apps_store.MANIFEST_KIND,
        "serial": 1,
        "apps": [{
            "id": "openharness-taos",
            "versions": [
                {
                    "version": "2.0.0",
                    "yanked": False,
                    "files": {
                        "macos-arm64": {"url": "openharness/2.0.0/oh-macos-arm64.zip", "sha256": "a" * 64, "size": 1000},
                        "linux-x64": {"url": "openharness/2.0.0/oh-linux-x64.zip", "sha256": "b" * 64, "size": 2000},
                    }
                },
                {
                    "version": "1.0.0",
                    "yanked": False,
                    "files": {
                        "macos-arm64": {"url": "openharness/1.0.0/oh-macos-arm64.zip", "sha256": "c" * 64, "size": 1500},
                        "linux-x64": {"url": "openharness/1.0.0/oh-linux-x64.zip", "sha256": "d" * 64, "size": 2500},
                    }
                },
            ]
        }]
    }


def test_validate_manifest_valid_returns_same_dict():
    """Valid manifest returns the same dict."""
    manifest = make_valid_manifest()
    result = apps_store.validate_manifest(manifest)
    assert result is manifest


def test_validate_manifest_kind_missing_raises_kind_error():
    """Kind missing raises DesktopManifestKindError."""
    manifest = make_valid_manifest()
    del manifest["kind"]
    with pytest.raises(apps_store.DesktopManifestKindError):
        apps_store.validate_manifest(manifest)


def test_validate_manifest_kind_wrong_raises_kind_error():
    """Kind 'firmware' raises DesktopManifestKindError (refuses firmware catalog replay)."""
    manifest = make_valid_manifest()
    manifest["kind"] = "firmware"
    with pytest.raises(apps_store.DesktopManifestKindError):
        apps_store.validate_manifest(manifest)


def test_validate_manifest_firmware_shaped_no_kind_raises_kind_error():
    """Firmware-shaped doc without kind raises DesktopManifestKindError."""
    manifest = {
        "serial": 1,
        "apps": [{
            "id": "x",
            "versions": [{"version": "1", "file": "x.bin"}]
        }]
    }
    with pytest.raises(apps_store.DesktopManifestKindError):
        apps_store.validate_manifest(manifest)


def test_validate_manifest_duplicate_app_id_raises():
    """Duplicate app id raises DesktopManifestInvalid."""
    manifest = make_valid_manifest()
    manifest["apps"].append({
        "id": "openharness-taos",
        "versions": [{"version": "1.0.0", "files": {"macos-arm64": {"url": "x.zip", "sha256": "e" * 64, "size": 1}}}]
    })
    with pytest.raises(apps_store.DesktopManifestInvalid):
        apps_store.validate_manifest(manifest)


def test_validate_manifest_empty_versions_raises():
    """Empty versions list raises DesktopManifestInvalid."""
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"] = []
    with pytest.raises(apps_store.DesktopManifestInvalid, match="openharness-taos"):
        apps_store.validate_manifest(manifest)


def test_validate_manifest_unknown_platform_key_raises():
    """Unknown platform key 'plan9-x64' raises DesktopManifestInvalid."""
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"][0]["files"]["plan9-x64"] = {"url": "x.zip", "sha256": "e" * 64, "size": 1}
    with pytest.raises(apps_store.DesktopManifestInvalid, match="plan9-x64"):
        apps_store.validate_manifest(manifest)


@pytest.mark.parametrize("bad_sha256", [
    "A" * 64,  # uppercase
    "a" * 63,  # too short
])
def test_validate_manifest_bad_sha256_raises(bad_sha256):
    """Invalid sha256 (uppercase or wrong length) raises DesktopManifestInvalid."""
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"][0]["files"]["macos-arm64"]["sha256"] = bad_sha256
    with pytest.raises(apps_store.DesktopManifestInvalid, match="sha256"):
        apps_store.validate_manifest(manifest)


@pytest.mark.parametrize("bad_size", [
    0, -1, True, "10",
])
def test_validate_manifest_bad_size_raises(bad_size):
    """Invalid size (0, negative, bool, string) raises DesktopManifestInvalid."""
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"][0]["files"]["macos-arm64"]["size"] = bad_size
    with pytest.raises(apps_store.DesktopManifestInvalid, match="size"):
        apps_store.validate_manifest(manifest)


# ------------------------------------------------------------------ pick_file ----
def test_pick_file_latest_picks_newer_version():
    """pick_file with no version picks the newer version."""
    manifest = make_valid_manifest()
    version_dict, file_entry = apps_store.pick_file(manifest, "openharness-taos", platform="macos-arm64")
    assert version_dict["version"] == "2.0.0"
    assert file_entry["url"] == "openharness/2.0.0/oh-macos-arm64.zip"


def test_pick_file_yanked_newest_picks_older():
    """When newest is yanked, latest picks the older one."""
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"][0]["yanked"] = True
    version_dict, file_entry = apps_store.pick_file(manifest, "openharness-taos", platform="macos-arm64")
    assert version_dict["version"] == "1.0.0"
    assert file_entry["url"] == "openharness/1.0.0/oh-macos-arm64.zip"


def test_pick_file_exact_yanked_version_returned():
    """Exact yanked version is still returned when requested by version."""
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"][0]["yanked"] = True
    version_dict, file_entry = apps_store.pick_file(manifest, "openharness-taos", version="2.0.0", platform="macos-arm64")
    assert version_dict["version"] == "2.0.0"
    assert file_entry["url"] == "openharness/2.0.0/oh-macos-arm64.zip"


def test_pick_file_unknown_app_raises_orberror():
    """Unknown app id raises OrbError naming known ids."""
    manifest = make_valid_manifest()
    with pytest.raises(OrbError, match="openharness-taos"):
        apps_store.pick_file(manifest, "unknown-app", platform="macos-arm64")


def test_pick_file_platform_no_file_raises_platform_unsupported():
    """Platform with no file in that version raises PlatformUnsupported naming available platforms."""
    manifest = make_valid_manifest()
    with pytest.raises(apps_store.PlatformUnsupported, match="macos-arm64"):
        apps_store.pick_file(manifest, "openharness-taos", platform="windows-x64")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])