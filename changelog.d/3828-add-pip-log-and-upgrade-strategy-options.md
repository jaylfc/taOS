### Fixed
- The licence drift gate no longer reads the value of `--log <file>` and `--upgrade-strategy <mode>` as a package name: both pip options were missing from the value-taking option set in `scripts/check_install_licences.py`, so their following token was flagged as an undeclared package.
