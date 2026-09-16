### Fixed

- CI now runs `uv lock --check` once per workflow invocation and fails the run when `pyproject.toml` and `uv.lock` have drifted, catching new dependencies that would otherwise be tested absent while installed in production.
