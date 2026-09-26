### Fixed
- Coerce numeric YAML versions to strings in `AppManifest` so manifests with `version: 8.0` (or similar) load correctly instead of being skipped with a schema validation error.
- Quote the `version` field in four model manifests that used bare floats.
