#!/usr/bin/env python3
"""One-shot migration of model manifests to the new requires.backends shape.

Reads each ``app-catalog/models/*/manifest.yaml`` and rewrites it:

- Adds ``context_window`` (top-level) from a curated lookup table.
- Adds ``variants[].requires.backends`` inferred from legacy
  ``install.method`` and ``variants[].backend``.
- Removes the legacy ``install`` block and ``variants[].backend`` field.
- Preserves ``hardware_tiers`` (now opaque metadata).

Run from repo root:

    python scripts/migrate-manifests-to-requires-backends.py [--dry-run]

Manual audit pass (separate, after running this) covers context_window
values not in the lookup table and any backend-specific quant edge cases.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Curated lookup — populated for the highest-traffic models. Anything not
# in here is left at 0 and flagged in the migration report for the manual
# audit pass to fix.
DEFAULT_CONTEXT_WINDOW: dict[str, int] = {
    "qwen2.5-3b": 32768,
    "qwen2.5-7b": 32768,
    "qwen2.5-14b": 32768,
    "qwen3-4b": 32768,
    "qwen3-8b": 32768,
    "gemma-3-1b": 32768,
    "gemma-3-4b": 128000,
    "gemma-3-12b": 128000,
    "deepseek-r1-14b": 32768,
    "deepseek-coder-v2-lite": 32768,
    "command-r-35b": 128000,
    "granite-3.1-2b": 32768,
    "granite-3.1-8b": 32768,
    "gemma-2-2b": 8192,
    "gemma-2-9b": 8192,
    "bge-large-en-v1.5": 512,
    "bge-m3": 8192,
    "bge-small-en-v1.5": 512,
    "bge-reranker-v2-m3": 8192,
    "qwen3-embedding-0.6b": 32768,
    "qwen3-reranker-0.6b": 32768,
}

# Backend ID → (targets, default-min-ram-fallback). RAM fallback is only used
# when the legacy variant doesn't declare ``min_ram_mb``.
BACKEND_TARGETS: dict[str, tuple[list[str], int]] = {
    "rkllama": (["rockchip"], 2048),
    "rk-llama-cpp": (["rockchip"], 2048),
    "ollama": (["apple-silicon", "x86-cuda", "x86-vulkan", "arm-vulkan", "cpu"], 4096),
    "llama-cpp": (["x86-vulkan", "arm-vulkan", "cpu"], 4096),
    "mlx": (["apple-silicon"], 4096),
    "vllm": (["x86-cuda"], 8192),
    "comfyui": (["x86-cuda", "x86-vulkan", "apple-silicon"], 4096),
    "transformers": (["x86-cuda", "x86-vulkan", "arm-vulkan", "cpu"], 8192),
}


def _backend_dep(bid: str, min_ram: int) -> dict:
    targets, fallback = BACKEND_TARGETS.get(bid, (["cpu"], 4096))
    return {
        "id": bid,
        "targets": list(targets),
        "min_ram_mb": int(min_ram or fallback),
    }


def infer_backends(manifest: dict, variant: dict) -> list[dict]:
    """Build a requires.backends list from legacy install.method + variant.backend."""
    method = (manifest.get("install") or {}).get("method")
    legacy_backends = list(variant.get("backend") or [])
    min_ram = int(variant.get("min_ram_mb") or 0)

    out: list[dict] = []

    # Method-driven mapping — special-cases for rkllama/rkllamacpp.
    if method == "rkllama":
        out.append(_backend_dep("rkllama", min_ram))
    elif method == "rkllamacpp":
        out.append(_backend_dep("rk-llama-cpp", min_ram))

    # Variant-declared backends — cumulative with the method-derived dep.
    for bid in legacy_backends:
        # Some manifests use "llama.cpp" with a dot — normalize.
        normalized = "llama-cpp" if bid in ("llama.cpp", "llama-cpp") else bid
        if any(d["id"] == normalized for d in out):
            continue
        out.append(_backend_dep(normalized, min_ram))

    return out


def migrate_manifest(path: Path, *, context_lookup: dict[str, int]) -> bool:
    """Rewrite a single manifest file in place. Returns True if changed."""
    raw = path.read_text()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return False
    if data.get("type") != "model":
        return False

    changed = False
    mid = data.get("id", "")

    # Set context_window if missing.
    if "context_window" not in data:
        data["context_window"] = int(context_lookup.get(mid, 0))
        changed = True

    # Rewrite each variant.
    for v in data.get("variants") or []:
        if not isinstance(v, dict):
            continue
        backends = infer_backends(data, v)
        if backends:
            v.setdefault("requires", {})
            v["requires"]["backends"] = backends
            changed = True
        if "backend" in v:
            del v["backend"]
            changed = True

    # Drop top-level install block (legacy).
    if "install" in data:
        del data["install"]
        changed = True

    if changed:
        path.write_text(
            yaml.safe_dump(
                data, sort_keys=False, allow_unicode=True, default_flow_style=False
            )
        )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("app-catalog/models"),
        help="Catalog directory to migrate (default: app-catalog/models)"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2

    changed: list[str] = []
    skipped: list[str] = []
    for manifest_path in sorted(args.root.rglob("manifest.yaml")):
        if args.dry_run:
            data = yaml.safe_load(manifest_path.read_text())
            if isinstance(data, dict) and data.get("type") == "model":
                changed.append(str(manifest_path))
            else:
                skipped.append(str(manifest_path))
            continue
        if migrate_manifest(manifest_path, context_lookup=DEFAULT_CONTEXT_WINDOW):
            changed.append(str(manifest_path))
        else:
            skipped.append(str(manifest_path))

    print(f"\nMigrated: {len(changed)} manifests")
    print(f"Skipped : {len(skipped)} manifests (non-model)")
    if not args.dry_run:
        # Flag manifests where context_window stayed at 0 — these need the
        # manual audit pass.
        zero_ctx: list[str] = []
        for p in changed:
            data = yaml.safe_load(Path(p).read_text())
            if isinstance(data, dict) and data.get("context_window", 0) == 0:
                zero_ctx.append(data.get("id", p))
        if zero_ctx:
            print(
                f"\n⚠ {len(zero_ctx)} manifests have context_window=0 — fix in audit pass:"
            )
            for mid in zero_ctx:
                print(f"  - {mid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
