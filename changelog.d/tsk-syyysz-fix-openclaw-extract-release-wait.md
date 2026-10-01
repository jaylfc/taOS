### Fixed

- Fixed openclaw version extract step to collect distinct versions from install.sh, failing when not exactly one is found
- Fixed release job to wait for the build matrix (`needs: [detect, build]`) and publish partial successes with `!cancelled()`
