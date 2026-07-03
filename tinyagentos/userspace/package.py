from __future__ import annotations

import io
import zipfile
from pathlib import Path

import yaml

from tinyagentos.userspace.capabilities import (
    KNOWN_CAPS,
    NET_ORIGIN_RE,
    NET_PREFIX,
    is_known_capability,
)

# A `network:<origin>` permission lets a granted app's bundle connect to that
# external origin (it is added to the sandbox CSP connect-src). The origin is
# strictly validated by NET_ORIGIN_RE (the canonical pattern in
# capabilities.py) so it can never inject extra CSP directives.

_ALLOWED_TYPES = {"web", "container", "tui"}
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
    if data["app_type"] == "tui":
        command = data.get("command")
        if not command or not isinstance(command, str) or not command.strip():
            raise PackageError("tui app requires a non-empty 'command' field")
        data["command"] = command.strip()
        args = data.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise PackageError("tui app 'args' must be a list of strings")
        data["args"] = args
        data.setdefault("needs_project_dir", True)
        if not isinstance(data["needs_project_dir"], bool):
            raise PackageError("tui app 'needs_project_dir' must be a boolean")
        env = data.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise PackageError("tui app 'env' must be a map of string to string")
        data["env"] = env
    data.setdefault("entry", "index.html")
    data.setdefault("icon", "")
    data.setdefault("permissions", [])
    if not isinstance(data["permissions"], list):
        raise PackageError("manifest 'permissions' must be a list")
    for perm in data["permissions"]:
        if not isinstance(perm, str):
            raise PackageError(
                f"manifest permission must be a string, got {type(perm).__name__}"
            )
        if perm.startswith(NET_PREFIX):
            origin = perm[len(NET_PREFIX):]
            if not NET_ORIGIN_RE.match(origin):
                raise PackageError(
                    f"invalid network permission origin {origin!r}: must be "
                    "wss://host or https://host with an optional *. subdomain "
                    "wildcard and an optional :port, nothing else"
                )
        elif not is_known_capability(perm):
            raise PackageError(
                f"unknown capability {perm!r}: allowed capabilities are "
                f"{sorted(KNOWN_CAPS)} or a network:<origin> grant"
            )
    return data


def build_package(manifest: dict, files: dict[str, str]) -> bytes:
    """Build a .taosapp zip (manifest.yaml + files) in memory, returning its bytes.

    The inverse of extract_package(). The manifest is validated with the same
    parse_manifest() rules extract_package() enforces on the way in, so a
    package built here is guaranteed importable by any other taOS instance's
    install endpoint. `files` maps a flat filename (no path separators) to its
    text content.
    """
    parse_manifest(yaml.safe_dump(manifest))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", yaml.safe_dump(manifest, sort_keys=False))
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


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
