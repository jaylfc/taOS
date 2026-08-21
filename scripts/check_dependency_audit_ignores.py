#!/usr/bin/env python3
"""Check that the dependency-audit ignore list is still valid.

Level-triggered: re-evaluates every run and reports while conditions hold.
Answers two questions every run:

1. Does a fixed version resolve yet?  Runs ``uv lock --upgrade-package``
   for each ignored package and reports success or failure with the
   compared command output.
2. Does pip-audit report any finding NOT in the ignore list?  Runs
   ``pip-audit`` without ``--ignore-vuln`` flags so every finding is
   visible, then compares each advisory id against the expected list.

Usage:
    python scripts/check_dependency_audit_ignores.py [--ignore-file path]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_IGNORE_FILE = Path(__file__).resolve().parent.parent / "security" / "pip-audit-ignore.toml"


def load_ignore_list(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return data.get("ignore", [])


def check_upgrade_resolves(package: str, project_root: Path) -> tuple[bool, str]:
    lock_path = project_root / "uv.lock"
    if not lock_path.is_file():
        return False, "uv.lock not found"
    backup = project_root / "uv.lock.bak"
    try:
        backup.write_bytes(lock_path.read_bytes())
        result = subprocess.run(
            ["uv", "lock", "--upgrade-package", package],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        detail = (result.stdout or result.stderr).strip()
        return result.returncode == 0, detail or f"exit {result.returncode}"
    except FileNotFoundError:
        return False, "uv not found"
    except subprocess.TimeoutExpired:
        return False, "uv lock timed out"
    finally:
        if backup.is_file():
            lock_path.write_bytes(backup.read_bytes())
            backup.unlink()


def run_pip_audit(project_root: Path) -> tuple[list[dict] | None, str]:
    """Return (findings, raw_output). ``findings`` is ``None`` when the audit
    could not be READ (unparseable output, timeout, missing binary) — a
    cannot-see state the caller must fail on, never fold into "no findings".
    An empty stdout with exit 0 is pip-audit's real no-findings shape."""
    cmd = ["pip-audit", "--format", "json"]
    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        findings: list[dict] = []
        stdout = result.stdout.strip()
        if stdout:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                return None, "unparseable pip-audit output: " + stdout[:300]
            for dep in data.get("dependencies", []):
                for vuln in dep.get("vulns", []):
                    findings.append(
                        {"package": dep.get("name"), "id": vuln.get("id")}
                    )
        elif result.returncode != 0:
            return None, "pip-audit failed with no output: " + (result.stderr or f"exit {result.returncode}")[:300]
        return findings, result.stdout + result.stderr
    except FileNotFoundError:
        return None, "pip-audit not found"
    except subprocess.TimeoutExpired:
        return None, "pip-audit timed out"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ignore-file",
        type=Path,
        default=DEFAULT_IGNORE_FILE,
        help="Path to the ignore list (default: security/pip-audit-ignore.toml)",
    )
    args = parser.parse_args(argv)

    for tool in ("uv", "pip-audit"):
        if not shutil.which(tool):
            print(f"error: {tool} not found in PATH", file=sys.stderr)
            return 2

    ignore_list = load_ignore_list(args.ignore_file)
    ignore_ids = {entry["id"] for entry in ignore_list}
    project_root = args.ignore_file.resolve().parent.parent
    # Cannot-see is not OK: without the lockfile every upgrade probe lands in
    # the benign NO FIX YET bucket and the run reports "current" while having
    # verified nothing. Same exit code as a missing tool — an environment
    # error, distinct from 1 (list genuinely stale).
    if not (project_root / "uv.lock").is_file():
        print(
            f"error: uv.lock not found under {project_root} — cannot verify the ignore list; refusing to report OK",
            file=sys.stderr,
        )
        return 2

    unresolved: list[tuple[str, str, str]] = []
    droppable: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str]] = []

    for entry in ignore_list:
        package = entry["package"]
        vid = entry["id"]
        if entry.get("check_upgrade") is False:
            skipped.append((package, vid))
            continue
        resolves, detail = check_upgrade_resolves(package, project_root)
        if resolves:
            droppable.append((package, vid, detail))
        else:
            unresolved.append((package, vid, detail))

    print("=== Fixed-version check ===")
    if skipped:
        for pkg, vid in skipped:
            print(f"SKIPPED: {pkg} ({vid}) — tool dependency, upgrade check not applicable")
    if droppable:
        for pkg, vid, detail in droppable:
            print(f"DROPPABLE: {pkg} ({vid}) — uv lock --upgrade-package {pkg}: {detail[:200]}")
    else:
        for pkg, vid, detail in unresolved:
            truncated = detail[:200] if detail else "no output"
            print(f"NO FIX YET: {pkg} ({vid}) — uv lock --upgrade-package {pkg}: {truncated}")

    print()
    print("=== pip-audit check ===")
    findings, raw = run_pip_audit(project_root)
    if findings is None:
        print(f"error: pip-audit result unreadable — {raw[:300]}", file=sys.stderr)
        return 2
    unlisted = [f for f in findings if f["id"] not in ignore_ids]
    if unlisted:
        for f in unlisted:
            print(f"UNLISTED: {f['package']} {f['id']}")
    else:
        if findings:
            listed = [f for f in findings if f["id"] in ignore_ids]
            print(f"OK: {len(findings)} finding(s), all in ignore list:")
            for f in listed:
                print(f"  {f['package']} {f['id']}")
        else:
            print("OK: no findings")

    print()
    if droppable or unlisted:
        print("FAIL: ignore list is stale or incomplete")
        return 1
    print("OK: ignore list is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
