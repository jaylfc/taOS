### Fixed

- `create_app`, the `recover-password` CLI, and all satellite modules now resolve the data directory through a single `resolve_data_dir()` function. Precedence: explicit `data_dir` argument > `TAOS_DATA_DIR` env var > `<project>/data` default. When both the env var and the explicit argument are set to different paths, the controller now refuses to start with a clear `ValueError` instead of silently picking one.
