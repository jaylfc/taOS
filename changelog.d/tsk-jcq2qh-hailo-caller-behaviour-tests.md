### Changed
- The installer callers of install-hailo.sh (`install_hailo_if_pending` in install-server.sh, the new `chain_hailo_installer` in install-worker.sh) are now tested by running them against stubbed exit codes 3, 1 and 0, replacing the source-text regex tests.
