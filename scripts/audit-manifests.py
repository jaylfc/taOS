#!/usr/bin/env python3
"""Audit catalog manifests for the requires.backends schema invariants.

After the migration to requires.backends, every model manifest must:
- declare context_window (non-zero)
- declare variants[].requires.backends with at least one entry
- not declare the deprecated install.method or variants[].backend fields
- only reference backend IDs that exist as service manifests
- only reference targets in the catalog-wide enum

Service manifests must NOT declare requires.backends (they are leaves
in the dependency graph).

Usage:
    python scripts/audit-manifests.py [--root app-catalog]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

VALID_TARGETS = {
    "rockchip-rk3588",
    "apple-silicon",
    "x86-cuda",
    "x86-vulkan",
    "arm-vulkan",
    "cpu",
}


def _load(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def audit(root: Path) -> int:
    issues: list[str] = []

    services_root = root / "services"
    backend_ids: set[str] = set()
    for sp in sorted(services_root.rglob("manifest.yaml")):
        sd = _load(sp)
        if not sd or sd.get("type") != "service":
            continue
        backend_ids.add(sd.get("id", sp.parent.name))
        if (sd.get("requires") or {}).get("backends"):
            issues.append(
                f"{sp}: service manifest declares requires.backends — "
                "backends must be leaves (one-level recursion guard)"
            )

    models_root = root / "models"
    for mp in sorted(models_root.rglob("manifest.yaml")):
        data = _load(mp)
        if not data or data.get("type") != "model":
            continue
        mid = data.get("id", mp.parent.name)

        if "install" in data and (data["install"] or {}).get("method"):
            issues.append(f"{mid}: deprecated install.method still present — migrate to requires.backends")

        if int(data.get("context_window") or 0) <= 0:
            issues.append(f"{mid}: context_window missing or 0 — populate from HF config.json")

        variants = data.get("variants") or []
        if not variants:
            issues.append(f"{mid}: model has no variants")
            continue

        for v in variants:
            if not isinstance(v, dict):
                continue
            vid = v.get("id", "?")
            if "backend" in v:
                issues.append(f"{mid}/{vid}: deprecated variants[].backend still present")
            deps = (v.get("requires") or {}).get("backends") or []
            if not deps:
                issues.append(f"{mid}/{vid}: requires.backends missing or empty")
                continue
            for d in deps:
                if not isinstance(d, dict):
                    continue
                bid = d.get("id", "?")
                if bid not in backend_ids:
                    issues.append(f"{mid}/{vid}: references unknown backend id {bid!r}")
                for t in d.get("targets") or []:
                    if t not in VALID_TARGETS:
                        issues.append(f"{mid}/{vid}: target {t!r} not in catalog enum")
                if int(d.get("min_ram_mb") or 0) <= 0:
                    issues.append(f"{mid}/{vid}: backend {bid!r} has min_ram_mb=0")

    if not issues:
        print("clean: catalog matches requires.backends schema")
        return 0
    print(f"\n{len(issues)} manifest issue(s):\n")
    for line in issues:
        print(f"  - {line}")
    print()
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("app-catalog"),
        help="Catalog root containing models/ and services/ (default: app-catalog)"
    )
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2
    return audit(args.root)


if __name__ == "__main__":
    sys.exit(main())
