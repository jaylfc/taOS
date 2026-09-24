### Fixed
- Fix first-party tag naming test to correctly parse `FROM` tag pins when the image name contains a version tag, so published tags are matched against the right image
- Handle `--platform`-prefixed `FROM` lines so arm64 pins are checked instead of silently skipped
- Glob `*.yaml` as well as `*.yml` for workflow publisher definitions
