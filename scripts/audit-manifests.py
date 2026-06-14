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
    "rockchip",
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

    # ------------------------------------------------------------------
    # install.script existence — a manifest that names an install script
    # which does not exist on disk produces a broken store install. The
    # two installer code paths resolve the path differently, so mirror them:
    #   - services -> ScriptInstaller resolves install.script against the
    #                 repo root (project_dir), e.g. scripts/install-x.sh
    #   - agents   -> deployer resolves against the manifest dir, and also
    #                 accepts tinyagentos/scripts/install_<id>.sh
    # Plugins that declare install.method: script may instead carry an inline
    # command in install.package or install.command (not a script file), so a
    # missing install.script is only flagged when none of those are present.
    repo_root = root.resolve().parent
    for sp in sorted(root.rglob("manifest.yaml")):
        d = _load(sp)
        if not d:
            continue
        inst = d.get("install") or {}
        if not isinstance(inst, dict) or inst.get("method") != "script":
            continue
        rel = sp.relative_to(root)
        kind = rel.parts[0] if rel.parts else ""
        mid = d.get("id", sp.parent.name)
        script = inst.get("script")
        if not script:
            if not (inst.get("package") or inst.get("command")):
                issues.append(
                    f"{kind}/{mid}: install.method=script but none of "
                    "install.script / install.package / install.command is set"
                )
            continue
        if kind == "services":
            candidates = [repo_root / script]
        else:
            candidates = [
                sp.parent / script,
                repo_root / "tinyagentos" / "scripts" / f"install_{mid}.sh",
            ]
        if not any(c.exists() for c in candidates):
            issues.append(
                f"{kind}/{mid}: install.script {script!r} not found "
                f"(checked: {', '.join(str(c) for c in candidates)})"
            )

    # ------------------------------------------------------------------
    # Port hygiene — pip/script services run ON THE HOST, so the port they
    # declare is the effective host bind. Those must live in the managed
    # high pool (>= 30000), never on a reserved/common port. Docker/LXC
    # apps are exempt: their host port comes from allocate_host_port and the
    # manifest port is container-internal.
    #
    # ALLOWLIST: LLM/inference/NPU backends that taOS connects to by a
    # hardcoded localhost URL (e.g. ollama on 11434, referenced across
    # resource_manager / job_worker / cluster probe). Moving these needs a
    # coordinated code+config change, tracked separately; they are not
    # user-installed store apps grabbing a core port.
    try:
        from tinyagentos.installers.port_allocator import (
            RESERVED_PORTS, _POOL_START, _POOL_END,
        )
    except Exception:  # pragma: no cover - keep the audit usable standalone
        _POOL_START = 30_000
        _POOL_END = 40_000
        RESERVED_PORTS = frozenset()
    PORT_HYGIENE_ALLOWLIST = frozenset({
        "ollama", "rkllama", "rk-llama-cpp", "vllm", "llama-cpp", "mlc-llm",
        "openllm", "ezrknpu",
    })
    for sp in sorted((root / "services").rglob("manifest.yaml")):
        d = _load(sp)
        if not d or d.get("type") != "service":
            continue
        if (d.get("install") or {}).get("method") not in ("pip", "script"):
            continue
        sid = d.get("id", sp.parent.name)
        if sid in PORT_HYGIENE_ALLOWLIST:
            continue
        declared = []
        for src in (d.get("ports"), (d.get("requires") or {}).get("ports")):
            if isinstance(src, list):
                declared += [p for p in src if isinstance(p, int)]
        for p in declared:
            if p in RESERVED_PORTS or p < _POOL_START or p >= _POOL_END:
                issues.append(
                    f"services/{sid}: host-binding {d['install']['method']} service "
                    f"declares port {p} outside the managed pool "
                    f"([{_POOL_START}, {_POOL_END})); remap to a high-pool port or "
                    "allowlist if it is an integrated backend"
                )

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
