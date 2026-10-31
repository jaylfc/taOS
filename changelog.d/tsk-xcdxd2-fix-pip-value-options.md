### Fixed

- pip install argument parsing in the licence gate: value options (`--retries`, `--timeout`, etc.) with no following value no longer swallow the next flag. `scripts/check_install_licences.py::undeclared_pip_installs` now advances by 1 when the next token is missing or starts with `-`, so flags are not incorrectly consumed.
