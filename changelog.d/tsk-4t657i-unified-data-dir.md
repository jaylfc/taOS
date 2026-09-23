### Fixed

- Unify data-dir resolution across `create_app`, `taos recover-password`, and all satellite modules under a single `resolve_data_dir()` helper. `TAOS_DATA_DIR` now takes precedence, and a mismatch between the environment variable and an explicit `--data-dir`/`data_dir=` argument refuses to start with a clear error instead of silently writing to two different directories.
