### Fixed
- Prevent `--no-binary` and `--only-binary` option values from being mistaken as package names in licence scanning; fix curl retry detection to avoid SIGPIPE under `set -e`.