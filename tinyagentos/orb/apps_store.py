"""Orb desktop apps store: manifest parse, validate, pick (spec s4.1)."""
import platform
import re
import urllib.parse
from pathlib import Path

from .frames import OrbError
from . import catalog


MANIFEST_KIND = "orb-desktop-apps"
PLATFORMS = ("macos-arm64", "macos-x64", "linux-x64", "linux-arm64", "windows-x64")


class DesktopManifestKindError(OrbError):
    """Manifest kind missing or not orb-desktop-apps."""


class DesktopManifestInvalid(OrbError):
    """Manifest shape error."""


class PlatformUnsupported(OrbError):
    """Host platform not supported or version has no file for it."""


def default_manifest_state_path(data_dir) -> Path:
    return Path(data_dir) / "orb" / "desktop_manifest_state.json"


def current_platform(system: str | None = None, machine: str | None = None) -> str:
    if system is None:
        system = platform.system()
    if machine is None:
        machine = platform.machine()

    os_map = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}
    arch_map = {
        "arm64": "arm64", "aarch64": "arm64",
        "x86_64": "x64", "amd64": "x64",
    }

    os_name = os_map.get(system)
    arch_name = arch_map.get(machine.lower())

    if os_name is None or arch_name is None:
        raise PlatformUnsupported(f"unsupported platform: {system} {machine}")

    platform_key = f"{os_name}-{arch_name}"
    if platform_key not in PLATFORMS:
        raise PlatformUnsupported(f"unsupported platform: {system} {machine}")

    return platform_key


def validate_relative_url(url) -> str:
    if not isinstance(url, str) or not url:
        raise DesktopManifestInvalid("url must be a non-empty string")

    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise DesktopManifestInvalid(f"url is malformed: {exc}") from exc
    if parts.scheme or parts.netloc:
        raise DesktopManifestInvalid("url must not have scheme or netloc")

    if url.startswith("/"):
        raise DesktopManifestInvalid("url must not start with /")

    if "\\" in url or "?" in url or "#" in url:
        raise DesktopManifestInvalid("url must not contain \\\\, ?, or #")

    for segment in url.split("/"):
        if segment in ("", ".", ".."):
            raise DesktopManifestInvalid("url segment must not be empty, ., or ..")

    return url


def resolve_url(base_url: str, rel: str) -> str:
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise DesktopManifestInvalid("base_url must start with https://")
    return base_url.rstrip("/") + "/" + validate_relative_url(rel)


def validate_manifest(doc) -> dict:
    if not isinstance(doc, dict) or doc.get("kind") != MANIFEST_KIND:
        raise DesktopManifestKindError("manifest kind missing or not orb-desktop-apps")

    apps = doc.get("apps")
    if not isinstance(apps, list):
        raise DesktopManifestInvalid("apps must be a list")

    seen_ids = set()
    for app in apps:
        if not isinstance(app, dict):
            raise DesktopManifestInvalid("app must be a dict")

        app_id = app.get("id")
        if not isinstance(app_id, str) or not app_id:
            raise DesktopManifestInvalid("app id must be a non-empty string")

        if app_id in seen_ids:
            raise DesktopManifestInvalid(f"duplicate app id: {app_id}")
        seen_ids.add(app_id)

        versions = app.get("versions")
        if not isinstance(versions, list) or not versions:
            raise DesktopManifestInvalid(f"{app_id}: versions must be a non-empty list")

        for version in versions:
            if not isinstance(version, dict):
                raise DesktopManifestInvalid(f"{app_id}: version must be a dict")

            ver_str = version.get("version")
            if not isinstance(ver_str, str) or not ver_str:
                raise DesktopManifestInvalid(f"{app_id}: version string must be a non-empty string")

            yanked = version.get("yanked")
            if yanked is not None and not isinstance(yanked, bool):
                raise DesktopManifestInvalid(f"{app_id} {ver_str}: yanked must be a bool")

            files = version.get("files")
            if not isinstance(files, dict) or not files:
                raise DesktopManifestInvalid(f"{app_id} {ver_str}: files must be a non-empty dict")

            for platform_key, file_entry in files.items():
                if platform_key not in PLATFORMS:
                    raise DesktopManifestInvalid(f"{app_id} {ver_str}: unknown platform {platform_key}")

                if not isinstance(file_entry, dict):
                    raise DesktopManifestInvalid(f"{app_id} {ver_str} {platform_key}: file entry must be a dict")

                try:
                    validate_relative_url(file_entry.get("url"))
                except DesktopManifestInvalid as e:
                    raise DesktopManifestInvalid(f"{app_id} {ver_str} {platform_key}: {e}") from e

                sha256 = file_entry.get("sha256")
                if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
                    raise DesktopManifestInvalid(f"{app_id} {ver_str} {platform_key}: sha256 must be 64 lowercase hex chars")

                size = file_entry.get("size")
                if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                    raise DesktopManifestInvalid(f"{app_id} {ver_str} {platform_key}: size must be int > 0")

    return doc


def pick_file(manifest: dict, app_id: str, version: str | None = None, platform: str | None = None) -> tuple[dict, dict]:
    apps = manifest.get("apps", [])
    app = None
    for a in apps:
        if a.get("id") == app_id:
            app = a
            break

    if app is None:
        known = ", ".join(sorted(a["id"] for a in apps)) or "none"
        raise OrbError(f"app {app_id!r} is not in the catalog (known: {known})")

    target_version = catalog.pick_version(app, version)
    platform = platform or current_platform()

    files = target_version.get("files", {})
    if platform not in files:
        available = ", ".join(sorted(files.keys())) or "none"
        raise PlatformUnsupported(f"{app_id} {target_version['version']}: no file for platform {platform} (available: {available})")

    return target_version, files[platform]