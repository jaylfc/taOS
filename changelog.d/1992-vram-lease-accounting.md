### Changed

- **cluster/manager.py** `claim_lease` (line ~845): VRAM admission now accounts for total required_vram_mb across all active leases on a worker, preventing over-commitment (H1).
- **scheduling/leases.py** `_renew_locked` (line ~90): Renewal rejects expired leases by checking `expires_at < now`, preventing lease resurrection (M1).
- **scheduler/discovery.py** `_gpu_vram_probe` (line ~217): Probe failure returns 0 instead of optimistic 999_999, closing the VRAM admission gap (M2).
- **vram_reservation.py** `_sweep_stale_unlocked` (line ~253): Synchronous sweep now guarded by `_thread_lock` to prevent dict-changed-during-iteration / lost updates from concurrent `available_vram()`/`stats()` calls via `asyncio.to_thread` (M3).
- **tests/test_1992_vram_lease_accounting.py**: New test file with 5 tests covering H1, M1, M2, and M3 — all verify the fix by failing against the buggy code.
