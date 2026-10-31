### Fixed
- scripts/install-server.sh: make curl retry flags compatible with curl < 7.71 (only add --retry-all-errors when supported) so key downloads do not abort on older curl versions; added retries and timeouts to pip installs and key download curls so flaky network connections do not abort the install.
