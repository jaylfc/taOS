### Fixed

- macOS launchd plist migration no longer widens exposure on a hand-written plist: a bare-uvicorn `com.tinyagentos.controller.plist` with no `--host` and/or `--port` is left unmigrated (it was defaulted to `TAOS_HOST=0.0.0.0` / `TAOS_PORT=6969`, wider than uvicorn's own `127.0.0.1:8000` default), and the update response carries a warning naming the plist so the installer can be re-run.
- If writing the one-shot reload helper or the pending-reload marker fails after the plist was replaced, the original plist is restored from `.bak`. Previously the new-format plist stayed on disk with no reload scheduled, so later updates saw it as already migrated and never retried.
- `launchctl bootstrap` of the reload helper in `_do_restart` is now bounded by a 15s timeout. A hung `launchctl` is killed and the controller falls through to the `execv` restart instead of blocking the restart indefinitely.
