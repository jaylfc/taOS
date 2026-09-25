### Fixed
- Hardened the body-authz gate to use AST parsing instead of regex, closing a fail-open where authz calls inside docstrings or string literals were incorrectly accepted as real authorization.
