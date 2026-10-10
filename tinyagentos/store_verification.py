from __future__ import annotations

import json
from pathlib import Path

from tinyagentos.installers.port_allocator import RESERVED_PORTS

_ALLOWED_USERSPACE_TYPES = {"web", "container", "tui"}

_REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "app_type")


def _find_manifest_path(app_dir: Path) -> Path | None:
    """Return the manifest file path if found, else None."""
    for name in ("manifest.yaml", "manifest.json"):
        p = app_dir / name
        if p.is_file():
            return p
    return None


def _parse_manifest_raw(text: str) -> dict | None:
    """Raw manifest parse without allowed_types validation.

    Used for non-userspace app types where parse_manifest would reject
    app_type values like 'native'.
    """
    if not text or not text.strip():
        return None
    try:
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            data = json.loads(text)
        else:
            import yaml

            data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            return None
        return data
    except (yaml.YAMLError, json.JSONDecodeError, ValueError):
        return None


def _is_userspace_app_type(app_type: str) -> bool:
    return app_type in _ALLOWED_USERSPACE_TYPES


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
        from tinyagentos.userspace.package import parse_manifest, PackageError

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
    # Also check for entry field for non-userspace types
    if not manifest.get("entry"):
        return {
            "name": "manifest_valid",
            "status": "fail",
            "detail": "manifest missing required field: entry",
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

    if not entry:
        # No build/entry declared -- warn
        return {
            "name": "builds",
            "status": "warn",
            "detail": "no build or entry declared",
        }

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
    On unexpected error, returns a 'fail' check named 'runner_error'.
    """
    try:
        app_dir_path = Path(app_dir).resolve()

        checks: list[dict] = []

        # 1. manifest_valid
        checks.append(_check_manifest_valid(app_dir_path))

        # 2. builds
        checks.append(_check_builds(app_dir_path))

        # 3. permissions_scan
        checks.append(_check_permissions_scan(app_dir_path))

        # 4. port_hygiene
        checks.append(_check_port_hygiene(app_dir_path))

        ok = all(c["status"] == "pass" for c in checks)

        return {"ok": ok, "checks": checks}

    except Exception as exc:
        return {
            "ok": False,
            "checks": [
                {
                    "name": "runner_error",
                    "status": "fail",
                    "detail": f"unexpected error: {exc}",
                }
            ],
        }