### Added

- Added `tests/sparkle_tests.bats`, a real bats suite covering the three Sparkle framework integration fixes: xcframework layout extraction in `fetch_sparkle.sh`, release-mode guard in `assemble_bundle.sh`, and Sparkle `binaryTarget` declaration in `Package.swift`.
- Wired the bats suite into `.github/workflows/ci.yml` so it runs on every push and PR.
- Sparkle bats suite runs the real `assemble_bundle.sh` (mirrored repo root, no in-test patching) and guards the `--release` arg-loop hang.
