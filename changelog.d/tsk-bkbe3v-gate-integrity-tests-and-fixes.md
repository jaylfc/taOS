### Added

- **Gate-integrity token env propagation test**: Exercises `main()` with no `--token` and `GITHUB_TOKEN` set in the environment, asserting the API layer receives the env token so unauthenticated and authorized calls are distinguishable.
- **Protected-path regression tests**: Parametrized `TestIsProtected` cases for `docs/doc-gate.toml`, `pyproject.toml`, and `tests/conftest.py`, each of which fails if the path is removed from `PROTECTED_PREFIXES`.

### Fixed

- **Docstring placement in `check_gate_integrity`**: Moved `token = token or _get_token()` to after the docstring so the docstring is not discarded into a string literal.
- **Pagination error messages in `_api_get`**: Error output now references `page_url` (the actual failing request) instead of the initial `url`, so paginated failures report the correct endpoint.
