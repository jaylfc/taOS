"""Orb desktop apps store: manifest parse, validate, pick (spec s4.1)."""
import asyncio
import hashlib
import os
import platform
import re
import tempfile
import urllib.parse
from pathlib import Path

import httpx

from tinyagentos import atomic_io
from tinyagentos.orb import catalog
from tinyagentos.orb import catalog_sig
from tinyagentos.orb.catalog_fetch import SIG_MAX_BYTES, _get_capped
from tinyagentos.orb.frames import OrbError


MANIFEST_KIND = "orb-desktop-apps"
PLATFORMS = ("macos-arm64", "macos-x64", "linux-x64", "linux-arm64", "windows-x64")
MANIFEST_MAX_BYTES = 1 << 20
APP_MAX_BYTES = 1 << 30
MANIFEST_FILE_NAME = "desktop.json"
MANIFEST_SIG_FILE_NAME = "desktop.json.sig"
_REL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AppDownloadError(OrbError):
    """App download failed."""


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
        if not _REL_SEGMENT_RE.match(segment):
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

            if "yanked" in version and not isinstance(version["yanked"], bool):
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


async def fetch_manifest(base_url, cache_dir, state_path, keys=None, timeout: float = 10.0, transport=None) -> dict:
    if not base_url or not base_url.startswith("https://"):
        raise catalog_sig.CatalogFetchError("base_url must start with https://")
    base = base_url.rstrip("/")
    manifest_url = f"{base}/{MANIFEST_FILE_NAME}"
    sig_url = f"{base}/{MANIFEST_SIG_FILE_NAME}"

    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport) as client:
                manifest_bytes = await _get_capped(client, manifest_url, MANIFEST_MAX_BYTES)
                if manifest_bytes is None:
                    raise catalog_sig.CatalogFetchError("manifest not found")
                sig_bytes = await _get_capped(client, sig_url, SIG_MAX_BYTES)
                if sig_bytes is None:
                    sig_bytes = None  # so accept_catalog raises CatalogSignatureMissing
    except TimeoutError as e:
        raise catalog_sig.CatalogFetchError(f"manifest fetch exceeded {timeout}s") from e

    # A signed firmware catalog replayed at the manifest URL must be refused by kind before it can raise the manifest's serial high-water mark.
    doc = catalog_sig.verify_catalog(manifest_bytes, sig_bytes, keys)
    validate_manifest(doc)
    catalog_sig.accept_catalog(manifest_bytes, sig_bytes, state_path, keys)

    cache_dir_path = Path(cache_dir)
    cache_dir_path.mkdir(parents=True, exist_ok=True)
    atomic_io.atomic_write_bytes(cache_dir_path / MANIFEST_FILE_NAME, manifest_bytes)
    atomic_io.atomic_write_bytes(cache_dir_path / MANIFEST_SIG_FILE_NAME, sig_bytes if sig_bytes is not None else b"")

    return doc


def load_cached_manifest(cache_dir, state_path, keys=None) -> dict:
    cache_dir_path = Path(cache_dir)
    manifest_path = cache_dir_path / MANIFEST_FILE_NAME
    sig_path = cache_dir_path / MANIFEST_SIG_FILE_NAME

    if not manifest_path.exists():
        raise catalog_sig.CatalogFetchError("no cached manifest")

    with manifest_path.open("rb") as f:
        manifest_bytes = f.read(MANIFEST_MAX_BYTES + 1)
    if len(manifest_bytes) > MANIFEST_MAX_BYTES:
        raise catalog_sig.CatalogFetchError("cached manifest exceeds cap")

    if not sig_path.exists():
        sig_bytes = None
    else:
        with sig_path.open("rb") as f:
            sig_bytes = f.read(SIG_MAX_BYTES + 1)
        if len(sig_bytes) > SIG_MAX_BYTES:
            raise catalog_sig.CatalogFetchError("cached manifest signature exceeds cap")
        if len(sig_bytes) == 0:
            sig_bytes = None

    # A signed firmware catalog replayed at the manifest URL must be refused by kind before it can raise the manifest's serial high-water mark.
    doc = catalog_sig.verify_catalog(manifest_bytes, sig_bytes, keys)
    validate_manifest(doc)
    catalog_sig.accept_catalog(manifest_bytes, sig_bytes, state_path, keys)
    return doc


async def download_app(manifest, app_id, base_url, dest_dir, version=None, platform=None, timeout: float = 300.0, transport=None) -> Path:
    v, f = pick_file(manifest, app_id, version, platform)
    if f["size"] > APP_MAX_BYTES:
        raise AppDownloadError("file exceeds app size limit")
    url = resolve_url(base_url, f["url"])
    dest_dir_path = Path(dest_dir)
    dest_dir_path.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir_path / f["url"].rsplit("/", 1)[-1]

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(dir=str(dest_dir_path), prefix=".dl-")
        os.close(fd)
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport) as client:
                    async with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            raise AppDownloadError(f"failed to download {url}: status {resp.status_code}")
                        content_encoding = resp.headers.get("content-encoding", "")
                        if content_encoding and content_encoding.lower() != "identity":
                            raise AppDownloadError(f"server returned compressed response for {url}: content-encoding {content_encoding}")
                        h = hashlib.sha256()
                        total = 0
                        expected = f["size"]
                        with open(temp_path, "wb") as tf:
                            async for chunk in resp.aiter_bytes():
                                total += len(chunk)
                                if total > expected:
                                    raise AppDownloadError("download exceeded declared size")
                                h.update(chunk)
                                tf.write(chunk)
        except AppDownloadError:
            raise
        except TimeoutError as e:
            raise AppDownloadError(f"download exceeded {timeout}s") from e
        except httpx.HTTPError as e:
            raise AppDownloadError(f"failed to download {url}: {e}") from e

        if total != expected or h.hexdigest() != f["sha256"]:
            raise AppDownloadError("download size or sha256 mismatch")
        os.replace(temp_path, final_path)
        temp_path = None
        return final_path
    except Exception:
        if temp_path is not None and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        raise


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