### Added

- Widen `requires-python` to `>=3.11,<3.15`, adding Python 3.14 support. The previous `<3.14` cap was stale: the pinned litellm 1.94.x (1.94.3 in uv.lock) supports `>=3.10,<3.15`, and Alpine edge ships only Python 3.14, so the old bound forced uv to provision a private 3.13 that could not import Alpine's `py3-onnxruntime` (built for 3.14).

### Changed

- `scripts/install-server.sh`: `pick_system_python` now accepts up to 3.14 while still preferring 3.13 when both exist. The stale-venv self-heal check recreates venvs using Python >=3.15 instead of >=3.14. The die message and litellm comment no longer claim 3.14 is unsupported.
- `scripts/install-server.sh`: on Alpine, `py3-onnxruntime` is installed via apk and the venv is created with `--system-site-packages` when the system interpreter is used, so the distro's `onnxruntime` binding (built for the system Python) is importable.

### Fixed

- CI: the full sharded suite still runs on 3.12 and 3.13 (plus 3.11 nightly); a new advisory `py314-import-smoke` job, modelled on `py311-import-smoke`, checks that the app and every route module import and compile on 3.14. It is not a required check.
