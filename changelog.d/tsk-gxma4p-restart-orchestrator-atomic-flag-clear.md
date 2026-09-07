### Fixed

- `restart_orchestrator.py` now writes the pending-restart flag and controller-side resume notes with `atomic_write_text`, so a power loss mid-write cannot produce a torn file.
- `apply_pending_restart_check` clears the pending-restart flag when `current_sha` is empty (unknown revision), preventing a stale flag from surviving an indefinite retry loop.
