from __future__ import annotations

import json
import yaml
from pathlib import Path

from tinyagentos.installers.port_allocator import RESERVED_PORTS
from tinyagentos.userspace.package import parse_manifest, PackageError, _ALLOWED_TYPES

_REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "app_type")


def _find_manifest_path(app_dir: Path) -> Path | None:
    """Return the manifest file path if found, else None."""
    for name in ("manifest.yaml", "manifest.json"):
        p = app_dir / name
        if p.is_file():
            return p
    return None


def _is_userspace_app_type(app_type: str) -> bool:
    return app_type in _ALLOWED_TYPES


def _check_manifest_valid(app_dir: Path) -> dict:
    """Check that the package has a valid manifest with required fields."""
    manifest_path = _find_manifest_path(app_dir)
    if manifest_path is None:
        return {"name": "manifest_valid", "status": "fail", "detail": "manifest missing or unparseable"}

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return {"name": "manifest_valid", "status": "fail", "detail": "manifest missing or unparseable"}

    if manifest_path.suffix == ".json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {"name": "manifest_valid", "status": "fail", "detail": "manifest missing or unparseable"}
        if not isinstance(data, dict):
            return {"name": "manifest_valid", "status": "fail", "detail": "manifest missing or unparseable"}
        manifest = data
    else:
        import yaml

        try:
            manifest = yaml.safe_load(text)
        except yaml.YAMLError:
            return {"name": "manifest_valid", "status": "fail", "detail": "manifest missing or unparseable"}
        if not isinstance(manifest, dict):
            return {"name": "manifest_valid", "status": "fail", "detail": "manifest missing or unparseable"}

    # If it's a userspace app type, validate using the full parser
    if _is_userspace_app_type(manifest.get("app_type")):
        try:
            parse_manifest(text)
        except PackageError as exc:
            return {
                "name": "manifest_valid",
                "status": "fail",
                "detail": f"manifest invalid: {exc}",
            }
        # parse_manifest already validated _REQUIRED_MANIFEST_FIELDS and app_type membership
        return {"name": "manifest_valid", "status": "pass", "detail": "valid"}

    # Non-userspace type: validate core fields directly
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if not manifest.get(f)]
    if missing:
        return {
            "name": "manifest_valid",
            "status": "fail",
            "detail": f"manifest missing required fields: {', '.join(missing)}",
        }
    return {"name": "manifest_valid", "status": "pass", "detail": "valid"}


def _raw_manifest(app_dir: Path) -> dict | None:
    """Parse manifest from app_dir using raw parser (no allowed_types check)."""
    manifest_path = _find_manifest_path(app_dir)
    if manifest_path is None:
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if manifest_path.suffix == ".json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return data
    else:
        import yaml

        try:
            data = yaml.safe_load(text)
            if not isinstance(data, dict):
                return None
            return data
        except yaml.YAMLError:
            return None


def _check_builds(app_dir: Path) -> dict:
    """Light static check that the declared entry/build target exists on disk."""
    manifest = _raw_manifest(app_dir)
    if manifest is None:
        return {"name": "builds", "status": "fail", "detail": "cannot read manifest for build check"}

    entry = manifest.get("entry")
    app_type = manifest.get("app_type")

    # If entry is set, resolve it and run containment check FIRST
    if entry:
        app_dir_resolved = app_dir.resolve()
        entry_path = (app_dir / entry).resolve()
        try:
            entry_path.relative_to(app_dir_resolved)
        except ValueError:
            return {
                "name": "builds",
                "status": "fail",
                "detail": "entry escapes package dir",
            }

    # Check for package.json with scripts.build
    pkg_json = app_dir / "package.json"
    if pkg_json.is_file():
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg_data.get("scripts", {})
            if isinstance(scripts, dict) and "build" in scripts:
                return {"name": "builds", "status": "pass", "detail": "package.json scripts.build declared"}
        except (json.JSONDecodeError, ValueError, OSError):
            pass

    if not entry:
        # No build/entry declared -- warn
        return {
            "name": "builds",
            "status": "warn",
            "detail": "no build or entry declared",
        }

    # Declared entry file missing
    entry_path = (app_dir / entry).resolve()
    if not entry_path.is_file():
        return {
            "name": "builds",
            "status": "fail",
            "detail": f"declared entry file missing: {entry}",
        }

    return {"name": "builds", "status": "pass", "detail": f"entry exists: {entry}"}


def _check_permissions_scan(app_dir: Path) -> dict:
    """Read manifest permissions; flag risky ones as warn with the specific permission named."""
    manifest = _raw_manifest(app_dir)
    if manifest is None:
        return {"name": "permissions_scan", "status": "fail", "detail": "cannot read manifest for permission scan"}

    perms = manifest.get("permissions", [])
    if not isinstance(perms, list):
        return {"name": "permissions_scan", "status": "fail", "detail": "manifest permissions is not a list"}

    risky = []
    for perm in perms:
        is_risky = False
        # Check for network:* wildcard (may have space after colon)
        if perm.startswith("network:") and perm[len("network:"):].strip().startswith("*"):
            is_risky = True
            risky.append(perm)
        # Check for exec capability
        elif perm == "exec":
            is_risky = True
            risky.append(perm)
        # Check for host filesystem
        elif perm == "host filesystem" or "host filesystem" in perm:
            is_risky = True
            risky.append(perm)

    if risky:
        details = "; ".join(risky)
        return {
            "name": "permissions_scan",
            "status": "warn",
            "detail": f"risky permissions: {details}",
        }

    return {"name": "permissions_scan", "status": "pass", "detail": "safe permissions only"}


def _check_port_hygiene(app_dir: Path) -> dict:
    """If the manifest declares ports, fail any that collide with core taOS ports
    (6969 controller, 7900 bus) or are in the reserved low range (<1024); pass otherwise."""
    manifest = _raw_manifest(app_dir)
    if manifest is None:
        return {"name": "port_hygiene", "status": "fail", "detail": "cannot read manifest for port hygiene"}

    # Get ports from manifest
    # Ports may be top-level or under container block for container apps
    ports = manifest.get("ports")
    if ports is None and manifest.get("app_type") == "container":
        container = manifest.get("container")
        if isinstance(container, dict):
            ports = container.get("ports")

    if not ports or not isinstance(ports, list):
        # No ports declared -- pass
        return {"name": "port_hygiene", "status": "pass", "detail": "no ports declared"}

    violations = []
    for port in ports:
        if not isinstance(port, int):
            continue
        # Check reserved low range (<1024)
        if port < 1024:
            violations.append(f"port {port} in reserved low range (<1024)")
            continue
        # Check core taOS ports
        if port in RESERVED_PORTS:
            violations.append(f"port {port} collides with core taOS port")

    if violations:
        details = "; ".join(violations)
        return {
            "name": "port_hygiene",
            "status": "fail",
            "detail": details,
        }

    return {"name": "port_hygiene", "status": "pass", "detail": "ports are hygienic"}


async def run_checks(app_dir: str | Path) -> dict:
    """Run verification checks against an app package directory.

    Returns a structured result:
    {ok: bool, checks: [{name, status ('pass'|'warn'|'fail'), detail}]}

    The function is pure + side-effect-free: it only reads app_dir and never raises.
    On unexpected error per-check, returns a 'fail' check named 'runner_error'.
    """
    app_dir_path = Path(app_dir).resolve()
    checks: list[dict] = []

    # 1. manifest_valid
    try:
        checks.append(_check_manifest_valid(app_dir_path))
    except Exception as exc:
        checks.append({
            "name": "runner_error",
            "status": "fail",
            "detail": f"unexpected error: {exc}",
        })

    # 2. builds
    try:
        checks.append(_check_builds(app_dir_path))
    except Exception as exc:
        checks.append({
            "name": "runner_error",
            "status": "fail",
            "detail": f"unexpected error: {exc}",
        })

    # 3. permissions_scan
    try:
        checks.append(_check_permissions_scan(app_dir_path))
    except Exception as exc:
        checks.append({
            "name": "runner_error",
            "status": "fail",
            "detail": f"unexpected error: {exc}",
        })

    # 4. port_hygiene
    try:
        checks.append(_check_port_hygiene(app_dir_path))
    except Exception as exc:
        checks.append({
            "name": "runner_error",
            "status": "fail",
            "detail": f"unexpected error: {exc}",
        })

    ok = all(c["status"] == "pass" for c in checks)

    return {"ok": ok, "checks": checks}
