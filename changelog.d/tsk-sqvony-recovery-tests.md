### Fixed
- Added per-app backend recovery tests for Activity, Files, Models, and Notes apps. Each test fakes a failed first load, triggers backend recovery via the health poll, and asserts the app's loader runs exactly once due to recovery.
