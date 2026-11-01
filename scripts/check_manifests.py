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


def lint_managed(root: Path) -> list[str]:
    """Return a list of human-readable errors for the managed-service contract.

    ``lifecycle.health.expect`` is a literal substring matched against the
    backend's health-endpoint response body (a 200 body must contain it).
    """
    # Validate every manifest against the same pydantic model the runtime
    # uses, so a typo'd manifest (e.g. ``requires: ollama`` as a string)
    # fails the gate instead of silently shipping. The CI mirror-image
    # bug was: `if not isinstance(lifecycle, dict): continue` skipped
    # malformed manifests, so a bad one passed the gate.
    from pydantic import ValidationError

    from tinyagentos.registry import AppManifest

    errors: list[str] = []
    services_dir = root / "services"
    for manifest in sorted(services_dir.glob("*/manifest.yaml")):
        sid_dir = manifest.parent.name
        # An unparseable or non-mapping manifest is an error, not a silent skip
        # (silently skipping gave a false "clean").
        try:
            data = yaml.safe_load(manifest.read_text())
        except yaml.YAMLError as exc:
            first = str(exc).splitlines()[0] if str(exc) else "parse error"
            errors.append(f"{sid_dir}: manifest.yaml is not valid YAML ({first[:120]})")
            continue
        if not isinstance(data, dict):
            errors.append(f"{sid_dir}: manifest.yaml top-level is not a mapping")
            continue

        # Boundary validation: same model the runtime loads with.
        try:
            AppManifest.model_validate({**data, "manifest_dir": manifest.parent})
        except ValidationError as exc:
            errors.append(f"{sid_dir}: manifest failed schema validation: {exc}")
            continue

        sid = str(data.get("id") or sid_dir)
        lifecycle = data.get("lifecycle")
        if not isinstance(lifecycle, dict):
            continue

        auto_manage = lifecycle.get("auto_manage")
        if not auto_manage:
            continue  # None / False / 0 / "" -> not claiming to be managed

        if sid in GRANDFATHER:
            continue

        # Truthy but not a real bool (e.g. quoted "true", 1) would otherwise slip
        # past an identity check and bypass the contract -- flag it, and still
        # enforce the rest since it is clearly meant to be managed.
        if auto_manage is not True:
            errors.append(
                f"{sid}: lifecycle.auto_manage must be a boolean true, got {auto_manage!r}"
            )

        missing: list[str] = []
        unit = lifecycle.get("unit")
        if not unit:
            missing.append("lifecycle.unit")
        elif not str(unit).endswith(".service"):
            errors.append(
                f"{sid}: lifecycle.unit '{unit}' should be a systemd unit name ending in .service"
            )

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
            expect = health.get("expect")
            if not expect:
                missing.append("lifecycle.health.expect")
            elif not isinstance(expect, str):
                errors.append(
                    f"{sid}: lifecycle.health.expect must be a string (a substring "
                    f"matched against the health response body), got {type(expect).__name__}"
                )

        if missing:
            errors.append(
                f"{sid}: lifecycle.auto_manage is true but is missing "
                f"{', '.join(missing)} (managed backends must declare their "
                f"systemd unit + health so taOS can manage them per node)"
            )
    return errors


def lint_model_fields(root: Path) -> tuple[list[str], list[str]]:
    """Return ``(warnings, errors)`` for model manifests.

    Missing capability fields are warnings, not errors: the 119 existing
    model manifests have not been backfilled yet, so a hard error would go
    red immediately. Once the backfill lands (Phase 2), promote the
    missing-``family`` warning to an error.

    A manifest this lint cannot read at all is an *error*: ``lint_managed``
    scans only ``services/``, so nothing else would report it and the gate
    would print "clean" over a broken model manifest. A gate that skips bad
    input cannot fail on it.

    Checked fields (taOS #146/#148 Phase 0):
    - ``family`` -- model family grouping (e.g. "qwen3").
    - ``min_context`` / ``max_context`` -- context window range (0 = unknown).
    - Per-variant ``vram_floor_mb`` and ``approx_tok_s_by_profile`` -- the
      capability envelope pick_variant() reads. Only warned when the variant
      has no envelope keys at all.
    """
    warnings: list[str] = []
    errors: list[str] = []
    models_dir = root / "models"
    if not models_dir.is_dir():
        return warnings, errors
    for manifest in sorted(models_dir.glob("*/manifest.yaml")):
        mid_dir = manifest.parent.name
        try:
            data = yaml.safe_load(manifest.read_text())
        except yaml.YAMLError as exc:
            first = str(exc).splitlines()[0] if str(exc) else "parse error"
            errors.append(f"{mid_dir}: manifest.yaml is not valid YAML ({first[:120]})")
            continue
        if not isinstance(data, dict):
            errors.append(f"{mid_dir}: manifest.yaml top-level is not a mapping")
            continue
        if data.get("type") != "model":
            continue
        mid = str(data.get("id") or mid_dir)
        if not data.get("family"):
            warnings.append(
                f"{mid}: missing 'family' (capability registry groups variants "
                f"by family; backfill in taOS #148 Phase 2)"
            )
        if not data.get("min_context") and not data.get("max_context"):
            warnings.append(
                f"{mid}: missing 'min_context'/'max_context' (context window "
                f"range; 0 = unknown)"
            )
        variants = data.get("variants") or []
        if isinstance(variants, list):
            for v in variants:
                if not isinstance(v, dict):
                    continue
                vid = str(v.get("id") or "?")
                if "vram_floor_mb" not in v and "approx_tok_s_by_profile" not in v:
                    warnings.append(
                        f"{mid} variant {vid}: no capability envelope "
                        f"('vram_floor_mb' / 'approx_tok_s_by_profile')"
                    )
    return warnings, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["managed-lint"])
    parser.add_argument("--root", default="app-catalog", help="catalog root dir")
    args = parser.parse_args(argv)

    root = Path(args.root)
    errors = lint_managed(root)
    warnings, model_errors = lint_model_fields(root)
    errors.extend(model_errors)
    for w in warnings:
        print(f"  warn: {w}", file=sys.stderr)
    if errors:
        print("MANIFEST-LINT FAIL:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("manifest managed-lint: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
