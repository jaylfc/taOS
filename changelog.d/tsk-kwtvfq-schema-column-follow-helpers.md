### Fixed

- `scripts/check_schema_column_migrations.py` now follows one level of same-file
  module-level helper calls from `_post_init` when checking for ALTER TABLE
  migrations. A guarded migration that lives in a module-level coroutine called
  by `_post_init` (the `agent_registry_store.py` pattern) no longer produces a
  false violation.
