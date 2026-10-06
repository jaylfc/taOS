### Added

- Fork audit now reads deployed pins from install scripts and reports drift for git forks and npm packages

### Fixed

- openclaw pin regex now matches the actual npm install command instead of a comment line; _read_pin skips comment lines to prevent parsing "(version)" as the version
