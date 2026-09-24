### Fixed
- Controller systemd unit generation: `install.sh` now correctly uses ``-m tinyagentos`` instead of ``-m uvicorn``, ensuring all installed units properly load the module's graceful-shutdown handler and avoiding infinite hangs during restarts on real deployments.
