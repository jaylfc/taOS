### Fixed
- CI now runs `uv lock --check` once per workflow run and fails when `pyproject.toml` declares a dependency that `uv.lock` lacks, preventing new dependencies from shipping tested only in their absence.
