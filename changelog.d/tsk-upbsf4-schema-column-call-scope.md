### Fixed

- `scripts/check_schema_column_migrations.py` now stops following same-file
  module-level helper calls at nested `def`/`class`/`lambda` boundaries. A
  call inside a never-executed helper defined within `_post_init` no longer
  silences a schema-column violation. The single-hop call follow matches the
  documented contract in `_post_init_added_columns` and the changelog.
