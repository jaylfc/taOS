#!/usr/bin/env python3
"""Catalog manifest lints that run in CI (separate from audit-manifests.py,
which is a one-off migration checker and is not wired into CI).

Currently implements the managed-backend-service contract lint (Phase 1 of the
cluster-aware backend service management design,
docs/design/cluster-backend-service-management.md):

    A service manifest whose ``lifecycle.auto_manage`` is true MUST declare
    ``lifecycle.unit`` (systemd unit name), ``lifecycle.scope`` ("system" or
    "user"), and ``lifecycle.health`` with ``url`` + ``expect``. This is what
    lets taOS treat the backend as a managed systemd service uniformly across
    cluster nodes (boot persistence, the Activity "Restart AI Services"
    recovery, and placement/migration all depend on it). Without the lint a
    backend can ship claiming auto_manage while giving taOS no unit to manage,
    which is how the production rkllama ended up a hand-started orphan.

Usage:
    python scripts/check_manifests.py managed-lint [--root app-catalog]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Services that declare auto_manage but are legitimately not yet a systemd unit
# on the host (or are managed by a non-manifest installer). Empty today; add an
# id here with a one-line reason only as a deliberate, temporary exception.
GRANDFATHER: dict[str, str] = {}

VALID_SCOPES = {"system", "user"}


def _load(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def lint_managed(root: Path) -> list[str]:
    """Return a list of human-readable errors for the managed-service contract."""
    errors: list[str] = []
    services_dir = root / "services"
    for manifest in sorted(services_dir.glob("*/manifest.yaml")):
        data = _load(manifest)
        if data is None:
            continue
        sid = str(data.get("id") or manifest.parent.name)
        lifecycle = data.get("lifecycle")
        if not isinstance(lifecycle, dict):
            continue
        if lifecycle.get("auto_manage") is not True:
            continue  # not claiming to be a managed service
        if sid in GRANDFATHER:
            continue

        missing: list[str] = []
        if not lifecycle.get("unit"):
            missing.append("lifecycle.unit")

        scope = lifecycle.get("scope")
        if not scope:
            missing.append("lifecycle.scope")
        elif scope not in VALID_SCOPES:
            errors.append(
                f"{sid}: lifecycle.scope is '{scope}', must be one of {sorted(VALID_SCOPES)}"
            )

        health = lifecycle.get("health")
        if not isinstance(health, dict):
            missing.append("lifecycle.health")
        else:
            if not health.get("url"):
                missing.append("lifecycle.health.url")
            if not health.get("expect"):
                missing.append("lifecycle.health.expect")

        if missing:
            errors.append(
                f"{sid}: lifecycle.auto_manage is true but is missing "
                f"{', '.join(missing)} (managed backends must declare their "
                f"systemd unit + health so taOS can manage them per node)"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["managed-lint"])
    parser.add_argument("--root", default="app-catalog", help="catalog root dir")
    args = parser.parse_args(argv)

    root = Path(args.root)
    errors = lint_managed(root)
    if errors:
        print("MANIFEST-LINT FAIL (managed-service contract):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("manifest managed-lint: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
