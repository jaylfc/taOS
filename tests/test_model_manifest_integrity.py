"""Sweep test for model-catalog manifest integrity.

Ensures every variant in app-catalog/models/*/manifest.yaml satisfies the
resolver schema contract: non-empty backends with known targets, a 64-char
lowercase hex sha256, a non-empty https download_url, and a positive size_mb.

A per-manifest allowlist tracks pre-existing sha256 debt until the catalog
is filled in.  The intent is zero entries.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

import yaml

# Known target enums -- the resolver only accepts values produced by
# hardware_to_targets in tinyagentos/cluster/capabilities.py. DERIVED from
# that source file rather than hardcoded: a literal copy silently drifts the
# moment a new backend target lands (adding "hailo" there would have made
# this sweep bounce the very PR that introduced it).
_CAPABILITIES_SRC = (
    Path(__file__).resolve().parent.parent
    / "tinyagentos" / "cluster" / "capabilities.py"
).read_text()
KNOWN_TARGETS = set(
    re.findall(r'targets\.append\(\s*"([a-z0-9-]+)"', _CAPABILITIES_SRC)
) | set(
    # conditional-expression appends: targets.append("a" if cond else "b")
    t
    for pair in re.findall(
        r'targets\.append\(\s*"([a-z0-9-]+)" if .+ else "([a-z0-9-]+)"',
        _CAPABILITIES_SRC,
    )
    for t in pair
)
assert len(KNOWN_TARGETS) >= 6, (
    f"target derivation collapsed ({sorted(KNOWN_TARGETS)}) - "
    "capabilities.py changed shape; fix the extraction, do not hardcode"
)

_SHA256_ALLOWLIST: set[str] = set()


def test_model_manifests_are_resolvable_and_integrity_pinned():
    root = Path(__file__).resolve().parent.parent / "app-catalog"
    errors: list[str] = []
    for path in sorted(glob.glob(str(root / "models" / "*" / "manifest.yaml"))):
        with open(path) as f:
            manifest = yaml.safe_load(f)
        mid = manifest.get("id") or Path(path).parent.name
        allowed_sha256 = mid in _SHA256_ALLOWLIST
        for variant in manifest.get("variants") or []:
            vid = variant.get("id", "<missing>")
            # Rule 1: requires.backends non-empty; every entry has non-empty
            # targets whose values are drawn from the known enum set.
            backends = ((variant.get("requires") or {}).get("backends")) or []
            if not backends:
                errors.append(f"{mid}/{vid}: requires.backends is empty")
                continue
            for backend in backends:
                targets = backend.get("targets") or []
                if not targets:
                    errors.append(
                        f"{mid}/{vid}: backend {backend.get('id')!r} has empty targets"
                    )
                else:
                    unknown = [t for t in targets if t not in KNOWN_TARGETS]
                    if unknown:
                        errors.append(
                            f"{mid}/{vid}: backend {backend.get('id')!r} has unknown targets {unknown}"
                        )
            # Rule 2: sha256 is a 64-char lowercase hex string.
            sha256 = variant.get("sha256")
            if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
                if not allowed_sha256:
                    errors.append(
                        f"{mid}/{vid}: sha256 must be a 64-char lowercase hex string (got {sha256!r})"
                    )
            # Rule 3: download_url is non-empty and parses as https.
            url = variant.get("download_url", "")
            if not url or not url.startswith("https://"):
                errors.append(
                    f"{mid}/{vid}: download_url must be a non-empty https URL (got {url!r})"
                )
            # Rule 4: size_mb is a positive int.
            size_mb = variant.get("size_mb")
            if not isinstance(size_mb, int) or size_mb <= 0:
                errors.append(
                    f"{mid}/{vid}: size_mb must be a positive int (got {size_mb!r})"
                )
    assert errors == [], (
        "model manifest integrity failures:\n" + "\n".join(errors)
    )
