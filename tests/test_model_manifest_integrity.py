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

# Patterns that indicate a download_url points to a single shard of a sharded
# model.  Variants whose download_url matches one of these patterns MUST also
# declare hf_repo + multi_file: true so the installer fetches the full shard
# set rather than a single fragment.
_SHARDED_URL_PATTERNS = [
    re.compile(r"model-\d+-of-\d+\.safetensors"),
]

# Known-fabricated sha256 placeholders that must never appear in a real
# manifest (under either sha256 or hef_h10h).  This class of error has
# recurred twice (#2425, #2451) because an LLM-generated placeholder digest
# looks hex-valid.  A denylist gate is the only reliable stop -- a prompt did
# not stop it, a gate will.
_FABRICATED_SHA256_DENYLIST: set[str] = {
    # llama-3.2-1b/a8w4 -- blocked in PR #2425
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
    # qwen3-1.7b/a8w4 -- blocked in PR #2425
    "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5",
}

# Variant-level keys that belong inside hardware_tiers, not as stray
# siblings.  If they appear at variant level, hardware_tiers parsed as null
# and the model is silently treated as unrestricted.
_TIER_KEY_RE = re.compile(r"^(arm|x86|cpu)-")


def _check_variant_integrity(
    mid: str, variant: dict, allowed_sha256: bool
) -> list[str]:
    """Validate a single variant dict against the manifest integrity rules.

    Returns a list of human-readable error strings (empty if the variant is
    valid).  Extracted from the sweep so the rules can be unit-tested with
    synthetic variants.
    """
    errors: list[str] = []
    vid = variant.get("id", "<missing>")

    # Rule 1: requires.backends non-empty; every entry has non-empty
    # targets whose values are drawn from the known enum set.
    backends = ((variant.get("requires") or {}).get("backends")) or []
    if not backends:
        errors.append(f"{mid}/{vid}: requires.backends is empty")
        return errors

    is_hailo_ollama = False
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
        if backend.get("id") == "hailo-ollama":
            is_hailo_ollama = True

    # Rule 2: content hash.  hailo-ollama variants reference the .hef
    # by the content hash field ``hef_h10h`` (pulled via
    # ``hailo-ollama pull`` and verified by the OllamaInstaller), not by
    # ``sha256`` + ``download_url``.
    if is_hailo_ollama:
        hef_h10h = variant.get("hef_h10h")
        if not re.fullmatch(r"[0-9a-f]{64}", hef_h10h or ""):
            errors.append(
                f"{mid}/{vid}: hef_h10h must be a 64-char lowercase hex "
                f"string (got {hef_h10h!r})"
            )
        if hef_h10h and hef_h10h in _FABRICATED_SHA256_DENYLIST:
            errors.append(
                f"{mid}/{vid}: hef_h10h {hef_h10h[:16]}... is a known-fabricated "
                f"placeholder (blocked in PR #2425)"
            )
        if variant.get("sha256") is not None:
            errors.append(
                f"{mid}/{vid}: hailo-ollama variants must not declare sha256 "
                f"(use hef_h10h for the content pin)"
            )
    else:
        multi_file = variant.get("multi_file") is True
        sha256 = variant.get("sha256")
        if multi_file:
            if sha256 is not None:
                errors.append(
                    f"{mid}/{vid}: multi_file variants must not declare sha256 "
                    f"(use file_set_hash for metadata pin); got {sha256!r}"
                )
        elif sha256 in _FABRICATED_SHA256_DENYLIST:
            errors.append(
                f"{mid}/{vid}: sha256 {sha256[:16]}... is a known-fabricated "
                f"placeholder (blocked in PR #2425)"
            )
        elif not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
            if not allowed_sha256:
                errors.append(
                    f"{mid}/{vid}: sha256 must be a 64-char lowercase hex string (got {sha256!r})"
                )
        # Rule 2b: multi_file variants must carry a 64-char lowercase
        # hex file_set_hash (metadata hash, not content SHA256).
        if multi_file:
            file_set_hash = variant.get("file_set_hash")
            if not re.fullmatch(r"[0-9a-f]{64}", file_set_hash or ""):
                errors.append(
                    f"{mid}/{vid}: multi_file variants require a 64-char "
                    f"lowercase hex file_set_hash (got {file_set_hash!r})"
                )
        # Rule 3: download_url must be a non-empty https URL.
        url = variant.get("download_url", "")
        if not url or not url.startswith("https://"):
            errors.append(
                f"{mid}/{vid}: download_url must be a non-empty https URL (got {url!r})"
            )
        else:
            for pat in _SHARDED_URL_PATTERNS:
                if pat.search(url):
                    hf_repo = variant.get("hf_repo")
                    multi_file = variant.get("multi_file") is True
                    if not hf_repo or not multi_file:
                        errors.append(
                            f"{mid}/{vid}: sharded download_url {url!r} requires "
                            f"hf_repo + multi_file: true"
                        )
                    break

    # Rule 4: size_mb must be a positive int.
    size_mb = variant.get("size_mb")
    if not isinstance(size_mb, int) or size_mb <= 0:
        errors.append(
            f"{mid}/{vid}: size_mb must be a positive int (got {size_mb!r})"
        )
    # Rule 5: tier keys (^(arm|x86|cpu)-) must nest under
    # hardware_tiers, not sit as stray siblings.  If
    # hardware_tiers is present it must be a non-empty mapping.
    stray_tier_keys = [
        k for k in variant if _TIER_KEY_RE.match(k)
    ]
    if stray_tier_keys:
        errors.append(
            f"{mid}/{vid}: tier key must nest under hardware_tiers; "
            f"stray variant-level keys {stray_tier_keys}"
        )
    if "hardware_tiers" in variant:
        # hardware_tiers is read at MANIFEST scope only
        # (cluster/capabilities.py, config.py); a variant-level block
        # is dead data that looks live -- exactly how PR #2453's
        # regression slipped past a variant-shape check.
        errors.append(
            f"{mid}/{vid}: hardware_tiers must sit at manifest scope, "
            f"not inside a variant (nothing reads it here)"
        )

    return errors


def test_model_manifests_are_resolvable_and_integrity_pinned():
    root = Path(__file__).resolve().parent.parent / "app-catalog"
    errors: list[str] = []
    for path in sorted(glob.glob(str(root / "models" / "*" / "manifest.yaml"))):
        with open(path) as f:
            manifest = yaml.safe_load(f)
        mid = manifest.get("id") or Path(path).parent.name
        allowed_sha256 = mid in _SHA256_ALLOWLIST
        for variant in manifest.get("variants") or []:
            errors.extend(
                _check_variant_integrity(mid, variant, allowed_sha256)
            )
        # Rule 6: manifest-scope hardware_tiers, when present, must be a
        # non-empty mapping, and tier keys must not sit stray at manifest
        # level either.
        stray_manifest_tier_keys = [
            k for k in (manifest or {}) if _TIER_KEY_RE.match(k)
        ]
        if stray_manifest_tier_keys:
            errors.append(
                f"{mid}: tier key must nest under hardware_tiers; "
                f"stray manifest-level keys {stray_manifest_tier_keys}"
            )
        if "hardware_tiers" in manifest:
            hw_tiers = manifest["hardware_tiers"]
            if not isinstance(hw_tiers, dict) or not hw_tiers:
                errors.append(
                    f"{mid}: hardware_tiers present but not a non-empty "
                    f"mapping (got {hw_tiers!r})"
                )
    assert errors == [], (
        "model manifest integrity failures:\n" + "\n".join(errors)
    )


class TestHailoHefPinIntegrity:
    """Unit tests for hef_h10h integrity on synthetic variants.

    The sweep test above exercises the real catalog; these tests assert
    the denylist and missing-pin rules directly so the logic is not only
    inferred from the presence of real manifests.
    """

    def _hailo_variant(self, **overrides) -> dict:
        v: dict = {
            "id": "a8w4",
            "format": "hef",
            "size_mb": 1790,
            "requires": {
                "backends": [
                    {"id": "hailo-ollama", "targets": ["hailo"], "min_ram_mb": 2048},
                ],
            },
        }
        v.update(overrides)
        return v

    def test_valid_hef_h10h_passes(self):
        errors = _check_variant_integrity(
            "m",
            self._hailo_variant(hef_h10h="0" * 64),
            False,
        )
        assert errors == []

    def test_missing_hef_h10h_rejected(self):
        """A hailo variant with no enforced pin must be rejected."""
        errors = _check_variant_integrity(
            "m", self._hailo_variant(), False
        )
        assert any("hef_h10h" in e for e in errors), (
            f"expected hef_h10h error, got {errors!r}"
        )

    def test_denylist_hef_h10h_rejected(self):
        """Each denylist digest must be rejected for hef_h10h."""
        for bad in _FABRICATED_SHA256_DENYLIST:
            errors = _check_variant_integrity(
                "m", self._hailo_variant(hef_h10h=bad), False
            )
            assert any("known-fabricated" in e for e in errors), (
                f"denylist digest {bad} was not rejected for hef_h10h: {errors!r}"
            )


def test_paligemma_2_file_set_hash_recompute_matches():
    """The manifest's file_set_hash must equal a recompute from the HF tree
    listing at the pinned hf_revision. This catches stale/incorrect pins.
    """
    from pathlib import Path
    from unittest.mock import patch

    from tinyagentos.installers.hf_multi_installer import _compute_combined_hash

    manifest_path = (
        Path(__file__).resolve().parent.parent
        / "app-catalog" / "models" / "paligemma-2" / "manifest.yaml"
    )
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)
    variant = next(v for v in manifest["variants"] if v["id"] == "safetensors-224")
    hf_repo = variant["hf_repo"]
    hf_revision = variant["hf_revision"]
    expected = variant["file_set_hash"]

    fixture = {
        "siblings": [
            {"rfilename": ".gitattributes", "size": 1570},
            {"rfilename": "README.md", "size": 27964},
            {"rfilename": "config.json", "size": 1332},
            {"rfilename": "generation_config.json", "size": 173},
            {
                "rfilename": "model-00001-of-00002.safetensors",
                "size": 4993319560,
                "lfs": {"sha256": "d66f653b186abdd1b3b092ac3d45efe94ddeda852615f8bf6766888e6ba7acc6"},
            },
            {
                "rfilename": "model-00002-of-00002.safetensors",
                "size": 1071263816,
                "lfs": {"sha256": "94ab5acf581f2afb3fe558bf98152ec572e4d66c6180fce4dae825e3b8ef4a9a"},
            },
            {"rfilename": "model.safetensors.index.json", "size": 75145},
            {"rfilename": "preprocessor_config.json", "size": 424},
            {"rfilename": "special_tokens_map.json", "size": 733},
            {
                "rfilename": "tokenizer.json",
                "size": 34600820,
                "lfs": {"sha256": "172fab587d68c56b63eb3620057c62dfd15e503079ff7fce584692e3fd5bf4da"},
            },
            {"rfilename": "tokenizer_config.json", "size": 242593},
        ]
    }

    def _stub_client(*_args, **_kwargs):
        class _Resp:
            def raise_for_status(self):
                return None
            def json(self):
                return fixture
        class _Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_exc):
                return False
            async def get(self, *_a, **_kw):
                return _Resp()
            async def aclose(self):
                return None
        return _Client()

    with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient", side_effect=_stub_client):
        import asyncio

        async def _fetch():
            from tinyagentos.installers.hf_multi_installer import list_hf_repo_files
            return await list_hf_repo_files(hf_repo, hf_revision)

        files = asyncio.run(_fetch())

    includes = variant.get("include_patterns") or []
    excludes = [
        ".gitattributes", "README.md", "LICENSE", "*.md", ".gitignore",
    ]
    import fnmatch
    from pathlib import Path

    selected = []
    for f in files:
        rfilename = f["rfilename"]
        name = rfilename.lstrip("/")
        if any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(Path(name).name, p) for p in excludes):
            continue
        if includes and not any(fnmatch.fnmatch(rfilename, p) for p in includes):
            continue
        selected.append(f)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target_dir = Path(tmp)
        computed = _compute_combined_hash(target_dir, selected)

    assert computed == expected, (
        f"file_set_hash mismatch: manifest has {expected}, recompute gave {computed}"
    )
