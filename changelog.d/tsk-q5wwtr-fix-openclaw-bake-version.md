### Fixed

- Fixed openclaw bake step so it passes OPENCLAW_VERSION to the container using `--env` flag
- Added validation to extract version step to fail when version is empty or doesn't match expected format (X.Y.Z)
