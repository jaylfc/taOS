### Fixed

- Auto-restart after an update now exits non-zero in Docker (`_do_restart` in `routes/system.py`), as #203 specifies: `always`/`unless-stopped` restart policies relaunch the container on any exit, but an `on-failure` policy only fires on a non-zero code, so the previous clean exit could leave an updated container stopped. Covered by a new `TestDoRestartDocker` test.
