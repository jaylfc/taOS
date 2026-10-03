### Fixed

- Two updater tests now unpack the 3-tuple `(returncode, output, launchd_warning)` returned by `_pip_rebuild_restart`, matching the signature change in PR #3345. The success message also spaces `launchd_warning` correctly before "Restarting now…" so a non-None warning no longer produces a double space or a missing space.
