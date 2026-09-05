### Added

- Reaper now reaps `executor.sh` processes older than CAP regardless of contained CLI, as an additional third rule alongside the existing CLI and orphan rules. This catches hung lanes whose parent process (e.g. dispatch_loop) is alive but neither a CLI nor orphaned.