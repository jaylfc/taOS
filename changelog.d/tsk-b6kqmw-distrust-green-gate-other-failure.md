### Fixed

- `distrust-green-gate` now has a dedicated `other_failure` comment branch for zero-collected-tests and setup/teardown errors instead of misleading contributors with the `Tests-Skipped-Intentionally` waiver trailer that cannot clear those cases.
- The failure reason is passed to the workflow script via `env` instead of inline `${{ }}` interpolation.
