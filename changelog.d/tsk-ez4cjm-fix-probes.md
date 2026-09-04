### Fixed

- Move `subscribe(_reconcile_auto_manage_lifecycle)` before `backend_catalog.start()` so the reconcile subscriber fires after the first probe (app.py)
- Reset `_initial_probe_done` in `start()` and `stop()` so each new polling cycle produces a fresh probe barrier (backend_catalog.py)