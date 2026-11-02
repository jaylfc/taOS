### Added

- Model manifests gain capability-registry fields (taOS #146/#148 Phase 0, schema only): `family` (model family grouping, e.g. "qwen3"), `min_context` / `max_context` (context window range, 0 = unknown), and documented per-variant envelope keys `vram_floor_mb` and `approx_tok_s_by_profile`. No manifests backfilled yet; `scripts/check_manifests.py` warns (does not fail) for model manifests missing these fields until the Phase 2 backfill lands.

### Fixed

- `scripts/check_manifests.py` now fails on a model manifest it cannot read (invalid YAML, or a top-level document that is not a mapping) instead of silently skipping it. `lint_managed` scans only `services/`, so skipping let the gate print `manifest managed-lint: clean` over a broken model manifest.
