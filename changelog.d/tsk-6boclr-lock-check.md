### Fixed
- CI now runs `uv lock --check` once per workflow run and fails if `pyproject.toml` and `uv.lock` have drifted apart, preventing a new dependency from being tested absent while shipped to users.
