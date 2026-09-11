### Added

- Added `assemble_bundle.sh` release-build smoke test verifying Sparkle.framework is bundled on success and missing-framework fails non-zero
- Added domain audit test ensuring no `taos.app` feed or download references remain under `mac/`

S2-23: Mac updater is a no-op: Sparkle never fetched; feed host is not the project domain
