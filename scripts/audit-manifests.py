#!/usr/bin/env python3
"""
Audit catalog manifests for empty backend or hardware_tiers fields.

The Store device/backend filter degrades gracefully on missing fields
(treats them as "no constraint"), but a manifest with empty backends
won't show up under any backend filter — and one with empty
hardware_tiers shows under every device, which is rarely intended.
This script flags them so we can fix them upstream.

Usage:
    python scripts/audit-manifests.py [--root app-catalog/models]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def audit(root: Path) -> int:
    issues: list[str] = []
    for manifest_path in sorted(root.rglob("manifest.yaml")):
        try:
            data = yaml.safe_load(manifest_path.read_text())
        except yaml.YAMLError as exc:
            issues.append(f"{manifest_path}: YAML parse error — {exc}")
            continue
        if not isinstance(data, dict):
            issues.append(f"{manifest_path}: not a mapping at top level")
            continue

        # Skip non-model entries — the filter is models-only for now.
        if data.get("type") != "model":
            continue

        mid = data.get("id", manifest_path.parent.name)
        variants = data.get("variants") or []
        method = (data.get("install") or {}).get("method")

        all_backends: set[str] = set()
        for v in variants:
            if isinstance(v, dict):
                for b in v.get("backend") or []:
                    if isinstance(b, str):
                        all_backends.add(b)
        if not all_backends and not method:
            issues.append(
                f"{mid} ({manifest_path}): no backends declared on any variant "
                f"and no install.method — model will not appear under any "
                f"backend filter"
            )

        tiers = data.get("hardware_tiers") or {}
        if not tiers:
            issues.append(
                f"{mid} ({manifest_path}): no hardware_tiers declared — "
                f"model will appear under every device filter (probably "
                f"unintended)"
            )

    if not issues:
        print("clean: every model manifest declares backends and hardware_tiers")
        return 0
    print(f"\n{len(issues)} manifest issue(s):\n")
    for line in issues:
        print(f"  - {line}")
    print()
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("app-catalog/models"),
        help="Catalog directory to scan (default: app-catalog/models)"
    )
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2
    return audit(args.root)


if __name__ == "__main__":
    sys.exit(main())
