from __future__ import annotations

import io
import zipfile
from pathlib import Path

import yaml

_ALLOWED_TYPES = {"web", "container"}
_REQUIRED = ("id", "name", "version", "app_type")

# Zip-bomb defenses: cap the declared uncompressed total, per-member size, and count.
_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_MEMBERS = 10000


class PackageError(Exception):
    """Raised when a .taosapp package is invalid or unsafe."""


def parse_manifest(text: str) -> dict:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PackageError(f"invalid manifest YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError(
            f"manifest.yaml must be a YAML mapping, got {type(data).__name__}"
        )
    for key in _REQUIRED:
        if not data.get(key):
            raise PackageError(f"manifest missing required field: {key}")
    if data["app_type"] not in _ALLOWED_TYPES:
        raise PackageError(
            f"app_type {data['app_type']!r} not allowed for userspace apps "
            f"(native is reserved for first-party core); use one of {_ALLOWED_TYPES}"
        )
    if data["app_type"] == "container":
        container = data.get("container")
        if not isinstance(container, dict):
            raise PackageError("container app requires a 'container' block")
        if not container.get("image") or not isinstance(container.get("image"), str):
            raise PackageError("container app requires container.image")
        ports = container.get("ports")
        if (
            not isinstance(ports, list)
            or len(ports) == 0
            or not all(isinstance(p, int) and not isinstance(p, bool) for p in ports)
        ):
            raise PackageError("container app requires container.ports as a non-empty list of ints")
    data.setdefault("entry", "index.html")
    data.setdefault("icon", "")
    data.setdefault("permissions", [])
    return data


def extract_package(data: bytes, apps_root: Path) -> dict:
    """Validate + extract a .taosapp zip into apps_root/{id}/. Returns the manifest."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageError("not a valid .taosapp (zip) archive") from exc
    infos = zf.infolist()
    if len(infos) > _MAX_MEMBERS:
        raise PackageError(f"package has too many files ({len(infos)} > {_MAX_MEMBERS})")
    total_uncompressed = 0
    for zi in infos:
        if zi.file_size > _MAX_MEMBER_BYTES:
            raise PackageError(f"package member too large: {zi.filename}")
        total_uncompressed += zi.file_size
    if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
        raise PackageError(
            f"package uncompressed size too large ({total_uncompressed} bytes)"
        )
    try:
        manifest = parse_manifest(zf.read("manifest.yaml").decode("utf-8"))
    except KeyError as exc:
        raise PackageError("manifest.yaml missing from package") from exc

    apps_root = Path(apps_root).resolve()
    app_dir = (apps_root / manifest["id"]).resolve()
    # app_dir itself must stay within apps_root (defends against a crafted id)
    if not app_dir.is_relative_to(apps_root) or app_dir == apps_root:
        raise PackageError(f"unsafe path in package: id {manifest['id']!r}")
    app_dir.mkdir(parents=True, exist_ok=True)
    for member in zf.namelist():
        if member.endswith("/"):
            continue
        dest = (app_dir / member).resolve()
        # dest must be a file strictly inside app_dir -- reject traversals and
        # members that resolve to app_dir itself (e.g. "." -> IsADirectoryError)
        if dest == app_dir or not dest.is_relative_to(app_dir):
            raise PackageError(f"unsafe path in package: {member}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(member))
    return manifest
