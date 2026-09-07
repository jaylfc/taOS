### Fixed

- `tinyagentos/registry`: `AppManifest` is now a `pydantic.BaseModel`, so
  manifests from the external catalog repo are type-checked at the load
  boundary. A wrong-typed field (e.g. `requires: "ollama"` where a mapping
  is required, `context_window: "8192"` where an int is required) now
  raises `pydantic.ValidationError` *naming the offending field and the
  manifest path* instead of silently propagating the bad value into
  install code as `AttributeError: 'str' object has no attribute 'get'`.
  The catalog load loop also skips a single bad manifest with a named
  log message instead of aborting the entire store listing, so one
  malformed entry no longer takes the store offline.
- `tinyagentos/registry`: a non-mapping YAML manifest (list, scalar, or
  empty file) is now skipped before `from_dict` is called, and
  `from_dict` itself raises `pydantic.ValidationError` (via
  `PydanticCustomError`) for non-mapping input instead of crashing with
  `TypeError`. The `from_file` path also uses `PydanticCustomError`
  instances for `yaml_parse_error` and `manifest_not_mapping` instead
  of invented error types that crashed inside pydantic-core.
- `scripts/check_manifests.py`: the CI lint now validates every service
  manifest against the same `AppManifest` pydantic model the runtime uses,
  closing the mirror-image bug where a typo'd manifest slipped through
  the gate as a silent skip.
- `.github/workflows/doc-gate.yml`: the managed-backend manifest lint
  step now installs `pydantic` alongside `pyyaml` so the script can
  import the model.
- `scripts/manifest.schema.json`: published JSON Schema for `AppManifest`
  so third-party app authors can validate their catalogs against the same
  contract the runtime enforces.