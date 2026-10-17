### Added

- Worker self-update: `scripts/taos-deploy-helper.sh` gains install, restart, health-check, and rollback subcommands, with a cgroup-safe detached restart (#1910).

### Fixed

- Worker self-update: an aborted update (failed checkpoint, pull, dependency update or restart) now clears the worker's draining state, so the worker returns to routing instead of heartbeating `draining` forever and staying unroutable until a manual restart (#1910).
- Worker self-update: the detached restart now waits for the worker to come back healthy and rolls back to the pre-update checkpoint when it does not, so a new build that fails to boot is rolled back even though the in-process rollback lives inside that build (#1910).
