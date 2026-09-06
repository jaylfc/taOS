### Fixed

- Define the one-shot lifecycle-reconcile subscriber before `backend_catalog.subscribe()` registers it, so app startup no longer dies with `UnboundLocalError` on a nested-function forward reference (app.py)
- Register that subscriber before `backend_catalog.start()` so the first probe pass cannot fire past an unregistered subscriber (app.py)
- `BackendCatalog.stop()` now sets the first-probe barrier before replacing it, releasing anyone parked in `wait_initial_probe()` instead of stranding them on an Event the cancelled poll task will never set (backend_catalog.py)
- `BackendCatalog.start()` no longer swaps the barrier, which could strand a caller that began awaiting `wait_initial_probe()` before `start()` ran (backend_catalog.py)
